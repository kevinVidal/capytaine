#!/usr/bin/env python
# coding: utf-8
"""
"""
# Copyright (C) 2017-2019 Matthieu Ancellin
# See LICENSE file at <https://github.com/mancellin/capytaine>

import logging
from functools import lru_cache

import numpy as np

from capytaine.tools.prony_decomposition import exponential_decomposition, error_exponential_decomposition
import capytaine.green_functions.Delhommeau_f90 as Delhommeau_f90
import capytaine.green_functions.XieDelhommeau_f90 as XieDelhommeau_f90

LOG = logging.getLogger(__name__)


class Delhommeau:
    """
    Parameters
    ----------
    tabulation_nb_integration_points: int, optional
        Number of points for the evaluation of the tabulated elementary integrals w.r.t. :math:`theta`
        used for the computation of the Green function (default: 251)
    finite_depth_prony_decomposition_method: string, optional
        The implementation of the Prony decomposition used to compute the finite depth Green function.

    Attributes
    ----------
    tabulated_integrals: 3-ple of arrays
        Tabulated integrals for the computation of the Green function.
    """

    fortran_core = Delhommeau_f90

    build_tabulated_integrals = lru_cache(maxsize=1)(Delhommeau_f90.initialize_green_wave.initialize_tabulated_integrals)

    def __init__(self,
                 tabulation_nb_integration_points=251,
                 finite_depth_prony_decomposition_method='fortran',
                 ):

        self.tabulated_integrals = self.__class__.build_tabulated_integrals(328, 46, tabulation_nb_integration_points)

        self.finite_depth_prony_decomposition_method = finite_depth_prony_decomposition_method

        self.exportable_settings = {
            'green_function': 'Delhommeau',
            'tabulation_nb_integration_points': tabulation_nb_integration_points,
            'finite_depth_prony_decomposition_method': finite_depth_prony_decomposition_method,
        }

        self._hash = hash(self.exportable_settings.values())

    def __hash__(self):
        return self._hash

    @lru_cache(maxsize=128)
    def find_best_exponential_decomposition(self, dimensionless_omega, dimensionless_wavenumber):
        """Compute the decomposition of a part of the finite depth Green function as a sum of exponential functions.

        Two implementations are available: the legacy Fortran implementation from Nemoh and a newer one written in Python.
        For some still unexplained reasons, the two implementations do not always give the exact same result.
        Until the problem is better understood, the Fortran implementation is the default one, to ensure consistency with Nemoh.
        The Fortran version is also significantly faster...

        Results are cached.

        Parameters
        ----------
        dimensionless_omega: float
            dimensionless angular frequency: :math:`kh \\tanh (kh) = \omega^2 h/g`
        dimensionless_wavenumber: float
            dimensionless wavenumber: :math:`kh`
        method: string, optional
            the implementation that should be used to compute the Prony decomposition

        Returns
        -------
        Tuple[np.ndarray, np.ndarray]
            the amplitude and growth rates of the exponentials
        """

        LOG.debug(f"\tCompute Prony decomposition in finite depth Green function "
                  f"for dimless_omega=%.2e and dimless_wavenumber=%.2e",
                  dimensionless_omega, dimensionless_wavenumber)

        if self.finite_depth_prony_decomposition_method.lower() == 'python':
            # The function that will be approximated.
            @np.vectorize
            def f(x):
                return Delhommeau_f90.initialize_green_wave.ff(x, dimensionless_omega, dimensionless_wavenumber)

            # Try different increasing number of exponentials
            for n_exp in range(4, 31, 2):

                # The coefficients are computed on a resolution of 4*n_exp+1 ...
                X = np.linspace(-0.1, 20.0, 4*n_exp+1)
                a, lamda = exponential_decomposition(X, f(X), n_exp)

                # ... and they are evaluated on a finer discretization.
                X = np.linspace(-0.1, 20.0, 8*n_exp+1)
                if error_exponential_decomposition(X, f(X), a, lamda) < 1e-4:
                    break

            else:
                LOG.warning("No suitable exponential decomposition has been found"
                            "for dimless_omega=%.2e and dimless_wavenumber=%.2e",
                            dimensionless_omega, dimensionless_wavenumber)

        elif self.finite_depth_prony_decomposition_method.lower() == 'fortran':
            lamda, a, nexp = Delhommeau_f90.old_prony_decomposition.lisc(dimensionless_omega, dimensionless_wavenumber)
            lamda = lamda[:nexp]
            a = a[:nexp]

        else:
            raise ValueError("Unrecognized method name for the Prony decomposition.")

        # Add one more exponential function (actually a constant).
        # It is not clear where it comes from exactly in the theory...
        a = np.concatenate([a, np.array([2])])
        lamda = np.concatenate([lamda, np.array([0.0])])

        return a, lamda

    def evaluate(self, mesh1, mesh2, free_surface=0.0, sea_bottom=-np.infty, wavenumber=1.0):

        depth = free_surface - sea_bottom
        if free_surface == np.infty: # No free surface, only a single Rankine source term

            a_exp, lamda_exp = np.empty(1), np.empty(1)  # Dummy arrays that won't actually be used by the fortran code.

            coeffs = np.array((1.0, 0.0, 0.0))

        elif depth == np.infty:

            a_exp, lamda_exp = np.empty(1), np.empty(1)  # Idem

            if wavenumber == 0.0:
                coeffs = np.array((1.0, 1.0, 0.0))
            elif wavenumber == np.infty:
                coeffs = np.array((1.0, -1.0, 0.0))
            else:
                coeffs = np.array((1.0, -1.0, 1.0))

        else:  # Finite depth
            a_exp, lamda_exp = self.find_best_exponential_decomposition(
                wavenumber*depth*np.tanh(wavenumber*depth),
                wavenumber*depth,
            )
            if wavenumber == 0.0:
                raise NotImplementedError
            elif wavenumber == np.infty:
                raise NotImplementedError
            else:
                coeffs = np.array((1.0, 1.0, 1.0))

        # Main call to Fortran code
        return self.fortran_core.matrices.build_matrices(
            mesh1.faces_centers, mesh1.faces_normals,
            mesh2.vertices,      mesh2.faces + 1,
            mesh2.faces_centers, mesh2.faces_normals,
            mesh2.faces_areas,   mesh2.faces_radiuses,
            wavenumber, 0.0 if depth == np.infty else depth,
            coeffs,
            *self.tabulated_integrals,
            lamda_exp, a_exp,
            mesh1 is mesh2
        )

################################

class XieDelhommeau(Delhommeau):
    fortran_core = XieDelhommeau_f90
    build_tabulated_integrals = lru_cache(maxsize=1)(XieDelhommeau_f90.initialize_green_wave.initialize_tabulated_integrals)
