import jax
import numpy as np
import pytest

from jaxoplanet.experimental.starry import Surface, Ylm
from jaxoplanet.experimental.starry.light_curves import light_curve, surface_light_curve
from jaxoplanet.experimental.starry.orbit import SurfaceSystem
from jaxoplanet.orbits import keplerian
from jaxoplanet.test_utils import assert_allclose
from jaxoplanet.light_curves import limb_dark_light_curve


@pytest.mark.parametrize("deg", [2, 5, 10])
@pytest.mark.parametrize("u", [[], [0.1], [0.2, 0.1]])
def test_compare_starry_limb_dark(deg, u):
    starry = pytest.importorskip("starry")
    starry.config.lazy = False

    # map
    inc = np.pi / 2
    np.random.seed(deg)
    y = Ylm.from_dense(np.random.randn((deg + 1) ** 2), normalize=True)
    map = Surface(y=y, u=u, inc=inc)

    # occultor
    yo = np.linspace(-3, 3, 1000)
    ro = 0.01
    zo = 2.0
    xo = 0.0

    # starry
    ms = starry.Map(ydeg=deg, udeg=len(u), inc=np.rad2deg(inc))
    ms[:, :] = y.todense()
    if len(u) > 0:
        ms[1:] = u
    expected = ms.flux(xo=xo, yo=yo, ro=ro, zo=zo)

    # jaxoplanet
    calc = jax.vmap(surface_light_curve, in_axes=(None, None, None, 0, None, None))(
        map, ro, xo, yo, zo, 0.0
    )

    assert_allclose(calc, expected)


@pytest.fixture(
    params=[
        {
            "central": keplerian.Central(
                mass=1.3,
                radius=1.1,
            ),
            "bodies": [{"radius": 0.5, "mass": 0.1, "period": 1.5}],
        },
        {
            "central": keplerian.Central(
                mass=1.3,
                radius=1.1,
            ),
            "central_surface": Surface(
                y=Ylm.from_dense(
                    [1, 0.005, 0.05, 0.09, 0.0, 0.1, 0.03, 0.04, 0.4, 0.2, 0.1]
                ),
                inc=0.9,
                obl=0.3,
                period=1.2,
                u=(0.1, 0.1),
            ),
            "bodies": [
                {
                    "radius": 0.5,
                    "mass": 0.1,
                    "period": 1.5,
                    "surface": Surface(
                        y=Ylm.from_dense([1, 0.005, 0.05, 0.09, 0.0, 0.1, 0.03]),
                        inc=-0.3,
                        period=0.8,
                        u=(0.2, 0.3),
                    ),
                }
            ],
        },
        {
            "central": keplerian.Central(
                mass=1.3,
                radius=1.1,
            ),
            "central_surface": Surface(
                y=Ylm.from_dense(
                    [1, 0.005, 0.05, 0.09, 0.0, 0.1, 0.03, 0.04, 0.4, 0.2, 0.1]
                ),
                inc=0.9,
                obl=0.3,
                period=1.2,
                u=(0.1, 0.1),
            ),
            "bodies": [
                {
                    "radius": 0.5,
                    "mass": 0.1,
                    "period": 1.5,
                },
                {
                    "radius": 0.2,
                    "mass": 0.3,
                    "period": 0.4,
                },
            ],
        },
        {
            "central": keplerian.Central(
                mass=1.3,
                radius=1.1,
            ),
            "central_surface": Surface(
                y=Ylm.from_dense(np.hstack([1, 0.005, 0.05, 0.09, 0.0, 0.1, 0.03])),
                period=1.2,
                u=(0.1, 0.1),
            ),
            "bodies": [
                {
                    "radius": 0.5,
                    "mass": 0.1,
                    "period": 1.5,
                }
            ],
        },
        {
            "central": keplerian.Central(
                mass=0.3,
                radius=0.1,
            ),
            "bodies": [
                {
                    "radius": 1.5,
                    "mass": 1.1,
                    "period": 1.5,
                    "surface": Surface(
                        y=Ylm.from_dense([1, 0.005, 0.05, 0.09, 0.0, 0.1, 0.03]),
                        period=1.2,
                        u=(0.1, 0.1),
                    ),
                }
            ],
        },
    ]
)
def keplerian_system(request):
    system = SurfaceSystem(
        request.param["central"],
        central_surface=request.param.get("central_surface", None),
    )
    for body in request.param["bodies"]:
        system = system.add_body(**body)

    return system


def test_compare_starry_system(keplerian_system):
    starry = pytest.importorskip("starry")
    starry.config.lazy = False

    def jaxoplanet2starry(body, surface_map=None):
        cls = (
            starry.Primary if isinstance(body, keplerian.Central) else starry.Secondary
        )
        if surface_map is None or surface_map.period is None:
            prot = 1e15
        else:
            prot = surface_map.period

        if surface_map is None:
            map_kwargs = dict(ydeg=0, rv=False, reflected=False)
        else:
            map_kwargs = dict(
                ydeg=surface_map.ydeg,
                udeg=surface_map.udeg,
                inc=np.rad2deg(surface_map.inc),
                obl=np.rad2deg(surface_map.obl),
                amp=surface_map.amplitude,
            )

        body_kwargs = dict(
            r=body.radius.magnitude,
            m=body.mass.magnitude,
            prot=prot,
        )

        if isinstance(body, keplerian.OrbitalBody):
            body_kwargs["porb"] = body.period.magnitude
            map_kwargs["amp"] = surface_map.amplitude if surface_map else 0.0

        starry_body = cls(starry.Map(**map_kwargs), **body_kwargs)

        if surface_map and surface_map.u:
            starry_body.map[1:] = surface_map.u
        if surface_map and surface_map.deg > 0:
            starry_body.map[1:, :] = np.asarray(surface_map.y.todense())[1:]

        return starry_body

    time = np.linspace(-1.5, 1.0, 300)

    # jaxoplanet
    jaxoplanet_flux = light_curve(keplerian_system)(time)

    # starry
    primary = jaxoplanet2starry(
        keplerian_system.central, keplerian_system.central_surface
    )
    secondaries = [
        jaxoplanet2starry(body, surface_map)
        for body, surface_map in zip(
            keplerian_system.bodies, keplerian_system.body_surfaces, strict=False
        )
    ]

    starry_system = starry.System(primary, *secondaries)
    starry_flux = starry_system.flux(time, total=False)

    assert_allclose(jaxoplanet_flux.T, np.array(starry_flux))

    # # compare summed jaxponet fluxes with the corresponding starry fluxes
    # if body_map is None:
    #     assert_allclose(jax.numpy.squeeze(jaxoplanet_flux), starry_flux[0])
    # elif central_map is None:
    #     assert_allclose(jax.numpy.squeeze(jaxoplanet_flux), starry_flux[1])
    # else:
    #     assert_allclose(jax.numpy.squeeze(jaxoplanet_flux),
    # np.sum(starry_flux, axis=0))

    # # compare individual jaxpolanet fluxes with the corresponding starry fluxes
    # for jaxoplanet_flux_item, starry_flux_item in zip(jaxoplanet_flux, starry_flux):
    #     if jaxoplanet_flux_item is not None:
    #         assert_allclose(jax.numpy.squeeze(jaxoplanet_flux_item), starry_flux_item)


def test_map_light_curves_none_occultor():
    surface = Surface(
        y=Ylm.from_dense(np.hstack([1, 0.005, 0.05, 0.09, 0.0, 0.1, 0.03])),
        period=1.2,
        u=(0.1, 0.1),
    )

    expected = surface_light_curve(surface, theta=0.5)
    calc = surface_light_curve(surface, 0.0, 2.0, 2.0, 2.0, theta=0.5)

    assert_allclose(calc, expected)


@pytest.mark.parametrize("deg", [2, 5, 10])
def test_compare_starry_rot(deg):
    starry = pytest.importorskip("starry")
    starry.config.lazy = False

    # map
    inc = np.pi / 2
    np.random.seed(deg)
    y = Ylm.from_dense(np.random.randn((deg + 1) ** 2), normalize=True)
    map = Surface(y=y, inc=inc)

    # phase
    theta = np.linspace(0, 2 * np.pi, 200)

    # starry
    ms = starry.Map(ydeg=deg, inc=np.rad2deg(inc))
    ms[:, :] = y.todense()
    expected = ms.flux(theta=np.rad2deg(theta))
    calc = jax.vmap(surface_light_curve, in_axes=(None, None, None, None, None, 0))(
        map, None, None, None, None, theta
    )

    assert_allclose(calc, expected)


def test_compare_limb_dark_light_curve():
    time = np.linspace(-0.1, 0.1, 200)

    params = {
        "stellar_mass": 0.3,
        "stellar_radius": 0.3,
        "planet_radius": 0.1,
        "u": (0.1, 0.1),
        "planet_period": 15.0,
    }

    system = keplerian.System(
        keplerian.Central(mass=params["stellar_mass"], radius=params["stellar_radius"]),
    )

    system = system.add_body(
        radius=params["planet_radius"], period=params["planet_period"]
    )

    expected = limb_dark_light_curve(system, params["u"])(time)[:, 0] + 1.0

    surface_system = SurfaceSystem(
        keplerian.Central(mass=params["stellar_mass"], radius=params["stellar_radius"]),
        Surface(u=params["u"]),
    )
    surface_system = surface_system.add_body(
        radius=params["planet_radius"], period=params["planet_period"]
    )

    calc = light_curve(surface_system)(time)[:, 0]

    assert_allclose(calc, expected)


@pytest.mark.parametrize("deg", [2, 5, 10])
def test_compare_y_from_u(deg):
    """In this test we convert the limb darkening coefficients to spherical harmonic
    coefficients and compare the light curve providing one or the other.
    """

    u = np.ones(deg)
    r = 0.1
    b = np.linspace(0, 1 + r, 1000)
    surface_u = Surface(u=u)
    surface_y = Surface(y=Ylm.from_limb_darkening(u))

    light_curve_function = jax.vmap(
        lambda surface, b: surface_light_curve(surface, r, y=b), (None, 0)
    )

    flux_u = light_curve_function(surface_u, b)
    flux_y = light_curve_function(surface_y, b)

    assert_allclose(flux_u, flux_y)
