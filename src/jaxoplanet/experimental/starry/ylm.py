import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import equinox as eqx
import jax
import jax.numpy as jnp
import numpy as np
from jax.experimental.sparse import BCOO
from scipy.special import legendre as LegendreP

from jaxoplanet.experimental.starry.wigner3j import Wigner3jCalculator
from jaxoplanet.types import Array


class Ylm(eqx.Module):
    data: dict[tuple[int, int], Array]
    """coefficients of the spherical harmonic expansion of the map in the form
    {(l, m): coefficient}"""
    ell_max: int = eqx.field(static=True)
    """maximum degree of the spherical harmonic expansion"""
    diagonal: bool = eqx.field(static=True)
    """whether the spherical harmonic expansion is diagonal (all m=0)"""

    def __init__(self, data: Mapping[tuple[int, int], Array]):
        # TODO(dfm): Think about how we want to handle the case of (0, 0). In
        # starry, that is always forced to be 1, and all the entries will be
        # normalized if an explicit value is provided. Do we want to enforce
        # that here too?
        self.data = dict(data)
        self.ell_max = max(ell for ell, _ in data.keys())
        self.diagonal = all(m == 0 for _, m in data.keys())

    @property
    def shape(self):
        """The number of coefficients in the expansion. This sets the shape of
        the output of `todense`."""
        return self.ell_max**2 + 2 * self.ell_max + 1

    @property
    def indices(self):
        return self.data.keys()

    def index(self, ell: Array, m: Array) -> Array:
        """Convert the degree and order of the spherical harmonic to the
        corresponding index in the coefficient array."""
        if np.any(np.abs(m) > ell):
            raise ValueError(
                "All spherical harmonic orders 'm' must be in the range [-ell, ell]"
            )
        return ell * (ell + 1) + m

    def tosparse(self) -> BCOO:
        indices, values = zip(*self.data.items())
        idx = np.array([self.index(ell, m) for ell, m in indices])[:, None]
        return BCOO((jnp.asarray(values), idx), shape=(self.shape,))

    def todense(self) -> Array:
        return self.tosparse().todense()

    @classmethod
    def from_dense(cls, y: Array) -> "Ylm":
        data = {}
        for i, ylm in enumerate(y):
            ell = int(np.floor(np.sqrt(i)))
            m = i - ell * (ell + 1)
            data[(ell, m)] = ylm
        return cls(data)

    def __mul__(self, other: Any) -> "Ylm":
        if isinstance(other, Ylm):
            return _mul(self, other)
        else:
            return jax.tree_util.tree_map(lambda x: x * other, self)

    def __rmul__(self, other: Any) -> "Ylm":
        assert not isinstance(other, Ylm)
        return jax.tree_util.tree_map(lambda x: other * x, self)

    def __getitem__(self, key) -> Array:
        assert isinstance(key, tuple)
        return self.todense()[self.index(*key)]


def _mul(f: Ylm, g: Ylm) -> Ylm:
    """
    Based closely on the implementation from the MIT-licensed spherical package:

    https://github.com/moble/spherical/blob/0aa81c309cac70b90f8dfb743ce35d2cc9ae6dee/spherical/multiplication.py
    """
    ellmax_f = f.ell_max
    ellmax_g = g.ell_max
    ellmax_fg = ellmax_f + ellmax_g
    fg = defaultdict(lambda *_: 0.0)
    m_calculator = Wigner3jCalculator(ellmax_f, ellmax_g)
    for ell1 in range(ellmax_f + 1):
        sqrt1 = math.sqrt((2 * ell1 + 1) / (4 * math.pi))
        for m1 in range(-ell1, ell1 + 1):
            idx1 = (ell1, m1)
            if idx1 not in f.data:
                continue
            sum1 = sqrt1 * f.data[idx1]
            for ell2 in range(ellmax_g + 1):
                sqrt2 = math.sqrt(2 * ell2 + 1)
                # w3j_s = s_calculator.calculate(ell1, ell2, s_f, s_g)
                for m2 in range(-ell2, ell2 + 1):
                    idx2 = (ell2, m2)
                    if idx2 not in g.data:
                        continue
                    w3j_m = m_calculator.calculate(ell1, ell2, m1, m2)
                    sum2 = sqrt2 * g.data[idx2]
                    m3 = m1 + m2
                    for ell3 in range(
                        max(abs(m3), abs(ell1 - ell2)), min(ell1 + ell2, ellmax_fg) + 1
                    ):
                        # Could loop over same (ell3, m3) more than once, so add all
                        # contributions together
                        fg[(ell3, m3)] += (
                            (
                                math.pow(-1, ell1 + ell2 + ell3 + m3)
                                * math.sqrt(2 * ell3 + 1)
                                * w3j_m[ell3]  # Wigner3j(ell1, ell2, ell3, m1, m2, -m3)
                            )
                            * sum1
                            * sum2
                        )
    return Ylm(fg)


def Bp(ydeg, npts: int = 1000, eps: float = 1e-9, smoothing=None):
    """
    Return the matrix B+. This expands the
    spot profile `b` in Legendre polynomials. From https://github.com/rodluger/
    mapping_stellar_surfaces/blob/paper2-arxiv/paper2/figures/spot_profile.py and
    _spot_setup in starry/_core/core.py.
    """
    if smoothing is None:
        if ydeg < 4:
            smoothing = 0.5
        else:
            smoothing = 2.0 / ydeg

    theta = jnp.linspace(0, jnp.pi, npts)
    cost = jnp.cos(theta)
    B = jnp.hstack(
        [
            jnp.sqrt(2 * l + 1) * LegendreP(l)(cost).reshape(-1, 1)
            for l in range(ydeg + 1)
        ]
    )
    _Bp = jnp.linalg.solve(B.T @ B + eps * jnp.eye(ydeg + 1), B.T)
    l = jnp.arange(ydeg + 1)
    indices = l * (l + 1)
    S = jnp.exp(-0.5 * indices * smoothing**2)
    return (S[:, None] * _Bp, theta, indices)


def spot_profile(theta, radius, spot_fac=300):
    """
    The sigmoid spot profile.

    """
    z = spot_fac * (theta - radius)
    return 1 / (1 + jnp.exp(-z)) - 1


def spot_ylm(ydeg):
    """
    spot expansion in the spherical harmonics basis.

    """
    B, theta, indices = Bp(ydeg)

    def func(contrast, r):
        b = spot_profile(theta, r)
        y = jnp.zeros((ydeg + 1) * (ydeg + 1))
        y = y.at[indices].set(B @ b * contrast)
        y = y.at[0].set(y[0] + 1.0)

        return Ylm.from_dense(y)

    return func


def ring_y(l_max, pts=1000, eps=1e-9, smoothing=None):
    """Ring expansion

    Parameters
    ----------
    l_max : int
        degree of the spherical harmonics
    pts : int, optional
        _description_, by default 1000
    eps : float, optional
        _description_, by default 1e-9
    fac : int, optional
        _description_, by default 300
    smoothing : float, optional
        _description_, by default None

    Returns
    -------
    Callable
        spherical harmonics coefficients of the ring expansion
    """
    Bp, theta, idxs = _Bp_expansion(l_max, pts=pts, eps=eps, smoothing=smoothing)
    n_max = l_max**2 + 2 * l_max + 1

    def _y(contrast: float, width: float, latitude: float):
        b = 1 - jnp.array((theta > latitude - width) & (theta < latitude + width))
        y = jnp.zeros(n_max)
        y = y.at[idxs].set(Bp @ b)
        return y * contrast

    return _y


def ring_y(l_max, pts=1000, eps=1e-9, smoothing=None):
    """Ring expansion

    Parameters
    ----------
    l_max : int
        degree of the spherical harmonics
    pts : int, optional
        _description_, by default 1000
    eps : float, optional
        _description_, by default 1e-9
    fac : int, optional
        _description_, by default 300
    smoothing : float, optional
        _description_, by default None

    Returns
    -------
    Callable
        spherical harmonics coefficients of the ring expansion
    """
    Bp, theta, idxs = _Bp_expansion(l_max, pts=pts, eps=eps, smoothing=smoothing)
    n_max = l_max**2 + 2 * l_max + 1

    def _y(contrast: float, width: float, latitude: float):
        b = 1 - jnp.array((theta > latitude - width) & (theta < latitude + width))
        y = jnp.zeros(n_max)
        y = y.at[idxs].set(Bp @ b)
        return y * contrast

    return _y
