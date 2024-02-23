# *starry*

```{warning}
Features described in these pages are experimental and API is subject to change
```

One of the goal of *jaxoplanet* is to provide the same features as [starry](), a framework to model light curves of non-uniform surfaces of stars and planets. The method behind *starry* consists in decomposing a surface using [spherical harmonics](https://en.wikipedia.org/wiki/Spherical_harmonics): a complete set of orthogonal functions that can serve as basis to represent any function defined on a sphere, meaning any stellar or planetary surface! Using analytical expressions, the *starry* papers describe ways to model different kind of time-series (such as light curves) as linear combinations of spherical harmonics, leading to fast and accurate inference capabilities.

```{toctree}
---
maxdepth: 2
---

api.md

```