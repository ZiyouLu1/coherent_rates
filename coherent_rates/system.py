from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import numpy as np
from scipy.constants import electron_volt, k
from surface_potential_analysis.basis.basis import (
    FundamentalBasis,
    FundamentalPositionBasis,
    FundamentalTransformedPositionBasis,
    FundamentalTransformedPositionBasis1d,
    TransformedPositionBasis1d,
)
from surface_potential_analysis.basis.evenly_spaced_basis import (
    EvenlySpacedBasis,
    EvenlySpacedTransformedPositionBasis,
)
from surface_potential_analysis.basis.stacked_basis import (
    StackedBasisLike,
    TupleBasis,
    TupleBasisWithLengthLike,
)
from surface_potential_analysis.basis.time_basis_like import EvenlySpacedTimeBasis
from surface_potential_analysis.dynamics.schrodinger.solve import (
    solve_schrodinger_equation_diagonal,
)
from surface_potential_analysis.hamiltonian_builder.momentum_basis import (
    total_surface_hamiltonian,
)
from surface_potential_analysis.operator.operator import (
    apply_operator_to_state,
    apply_operator_to_states,
)
from surface_potential_analysis.potential.conversion import convert_potential_to_basis
from surface_potential_analysis.stacked_basis.build import (
    fundamental_stacked_basis_from_shape,
)
from surface_potential_analysis.stacked_basis.conversion import (
    stacked_basis_as_fundamental_momentum_basis,
    stacked_basis_as_fundamental_position_basis,
)
from surface_potential_analysis.state_vector.conversion import (
    convert_state_vector_to_basis,
)
from surface_potential_analysis.state_vector.plot import (
    get_periodic_x_operator,
)
from surface_potential_analysis.state_vector.state_vector_list import (
    calculate_inner_products_elementwise,
)
from surface_potential_analysis.wavepacket.get_eigenstate import (
    get_full_bloch_hamiltonian,
)
from surface_potential_analysis.wavepacket.wavepacket import (
    BlochWavefunctionListWithEigenvaluesList,
    generate_wavepacket,
)

if TYPE_CHECKING:
    from surface_potential_analysis.basis.explicit_basis import (
        ExplicitStackedBasisWithLength,
    )
    from surface_potential_analysis.operator.operator import (
        SingleBasisDiagonalOperator,
        SingleBasisOperator,
    )
    from surface_potential_analysis.potential.potential import Potential
    from surface_potential_analysis.state_vector.eigenstate_collection import ValueList
    from surface_potential_analysis.state_vector.state_vector import StateVector
    from surface_potential_analysis.state_vector.state_vector_list import (
        StateVectorList,
    )

_L0Inv = TypeVar("_L0Inv", bound=int)
_L1Inv = TypeVar("_L1Inv", bound=int)


@dataclass
class PeriodicSystem:
    """Represents the properties of a 1D Periodic System."""

    id: str
    """A unique ID, for use in caching"""
    barrier_energy: float
    lattice_constant: float
    mass: float


@dataclass
class PeriodicSystemConfig:
    """Configure the simlation-specific detail of the system."""

    shape: tuple[int]
    resolution: tuple[int]
    n_bands: int


HYDROGEN_NICKEL_SYSTEM = PeriodicSystem(
    id="HNi",
    barrier_energy=2.5593864192e-20,
    lattice_constant=2.46e-10 / np.sqrt(2),
    mass=1.67e-27,
)

SODIUM_COPPER_SYSTEM = PeriodicSystem(
    id="NaCu",
    barrier_energy=55e-3 * electron_volt,
    lattice_constant=3.615e-10,
    mass=3.8175458e-26,
)

LITHIUM_COPPER_SYSTEM = PeriodicSystem(
    id="LiCu",
    barrier_energy=45e-3 * electron_volt,
    lattice_constant=3.615e-10,
    mass=1.152414898e-26,
)


def get_potential(
    system: PeriodicSystem,
) -> Potential[TupleBasis[FundamentalTransformedPositionBasis1d[Literal[3]]]]:
    delta_x = np.sqrt(3) * system.lattice_constant / 2
    axis = FundamentalTransformedPositionBasis1d[Literal[3]](np.array([delta_x]), 3)
    vector = 0.25 * system.barrier_energy * np.array([2, -1, -1]) * np.sqrt(3)
    return {"basis": TupleBasis(axis), "data": vector}


def get_interpolated_potential(
    system: PeriodicSystem,
    resolution: tuple[_L0Inv],
) -> Potential[
    StackedBasisLike[FundamentalTransformedPositionBasis[_L0Inv, Literal[1]]]
]:
    potential = get_potential(system)
    old = potential["basis"][0]
    basis = TupleBasis(
        TransformedPositionBasis1d[_L0Inv, Literal[3]](
            old.delta_x,
            old.n,
            resolution[0],
        ),
    )
    scaled_potential = potential["data"] * np.sqrt(resolution[0] / old.n)
    return convert_potential_to_basis(
        {"basis": basis, "data": scaled_potential},
        stacked_basis_as_fundamental_momentum_basis(basis),
    )


def get_extended_interpolated_potential(
    system: PeriodicSystem,
    shape: tuple[_L0Inv],
    resolution: tuple[_L1Inv],
) -> Potential[
    TupleBasisWithLengthLike[
        EvenlySpacedTransformedPositionBasis[_L1Inv, _L0Inv, Literal[0], Literal[1]]
    ]
]:
    interpolated = get_interpolated_potential(system, resolution)
    old = interpolated["basis"][0]
    basis = TupleBasis(
        EvenlySpacedTransformedPositionBasis[_L1Inv, _L0Inv, Literal[0], Literal[1]](
            old.delta_x * shape[0],
            n=old.n,
            step=shape[0],
            offset=0,
        ),
    )
    scaled_potential = interpolated["data"] * np.sqrt(basis.fundamental_n / old.n)

    return {"basis": basis, "data": scaled_potential}


def _get_full_hamiltonian(
    system: PeriodicSystem,
    shape: tuple[_L0Inv],
    resolution: tuple[_L0Inv],
    *,
    bloch_fraction: np.ndarray[tuple[Literal[1]], np.dtype[np.float64]] | None = None,
) -> SingleBasisOperator[StackedBasisLike[FundamentalPositionBasis[int, Literal[1]]],]:
    bloch_fraction = np.array([0]) if bloch_fraction is None else bloch_fraction

    potential = get_extended_interpolated_potential(system, shape, resolution)
    converted = convert_potential_to_basis(
        potential,
        stacked_basis_as_fundamental_position_basis(potential["basis"]),
    )
    return total_surface_hamiltonian(converted, system.mass, bloch_fraction)


def _get_bloch_wavefunctions(
    system: PeriodicSystem,
    config: PeriodicSystemConfig,
) -> BlochWavefunctionListWithEigenvaluesList[
    EvenlySpacedBasis[int, int, int],
    StackedBasisLike[FundamentalBasis[int]],
    StackedBasisLike[FundamentalPositionBasis[int, Literal[1]]],
]:
    def hamiltonian_generator(
        bloch_fraction: np.ndarray[tuple[Literal[1]], np.dtype[np.float64]],
    ) -> SingleBasisOperator[
        StackedBasisLike[FundamentalPositionBasis[int, Literal[1]]]
    ]:
        return _get_full_hamiltonian(
            system,
            (1,),
            config.resolution,
            bloch_fraction=bloch_fraction,
        )

    TupleBasis(FundamentalBasis)
    return generate_wavepacket(
        hamiltonian_generator,
        save_bands=EvenlySpacedBasis(config.n_bands, 1, 0),
        list_basis=fundamental_stacked_basis_from_shape(config.shape),
    )


def get_hamiltonian(
    system: PeriodicSystem,
    config: PeriodicSystemConfig,
) -> SingleBasisDiagonalOperator[ExplicitStackedBasisWithLength[Any, Any]]:
    wavefunctions = _get_bloch_wavefunctions(system, config)

    return get_full_bloch_hamiltonian(wavefunctions)


_AX0Inv = TypeVar("_AX0Inv", bound=EvenlySpacedTimeBasis[Any, Any, Any])


def solve_schrodinger_equation(
    system: PeriodicSystem,
    config: PeriodicSystemConfig,
    initial_state: StateVector[Any],
    times: _AX0Inv,
) -> StateVectorList[_AX0Inv, ExplicitStackedBasisWithLength[Any, Any]]:
    hamiltonian = get_hamiltonian(system, config)
    converted_initial = convert_state_vector_to_basis(
        initial_state,
        hamiltonian["basis"][0],
    )

    return solve_schrodinger_equation_diagonal(converted_initial, times, hamiltonian)


def get_isf(
    system: PeriodicSystem,
    config: PeriodicSystemConfig,
    initial_state: StateVector[Any],
    times: _AX0Inv,
    direction: tuple[int] = (1,),
) -> ValueList[_AX0Inv]:
    operator = get_periodic_x_operator(
        initial_state["basis"],
        direction=direction,
    )

    state_evolved = solve_schrodinger_equation(system, config, initial_state, times)

    state_evolved_scattered = apply_operator_to_states(operator, state_evolved)

    state_scattered = apply_operator_to_state(operator, initial_state)

    state_scattered_evolved = solve_schrodinger_equation(
        system,
        config,
        state_scattered,
        times,
    )
    return calculate_inner_products_elementwise(
        state_scattered_evolved,
        state_evolved_scattered,
    )


def get_isf_from_hamiltonian(
    hamiltonian: SingleBasisDiagonalOperator[Any],
    operator: SingleBasisOperator[Any],
    initial_state: StateVector[Any],
    times: _AX0Inv,
) -> ValueList[_AX0Inv]:
    converted_initial = convert_state_vector_to_basis(
        initial_state,
        hamiltonian["basis"][0],
    )

    state_evolved = solve_schrodinger_equation_diagonal(
        converted_initial,
        times,
        hamiltonian,
    )

    state_evolved_scattered = apply_operator_to_states(operator, state_evolved)

    state_scattered = apply_operator_to_state(operator, initial_state)

    converted_scattered = convert_state_vector_to_basis(
        state_scattered,
        hamiltonian["basis"][0],
    )

    state_scattered_evolved = solve_schrodinger_equation_diagonal(
        converted_scattered,
        times,
        hamiltonian,
    )

    return calculate_inner_products_elementwise(
        state_scattered_evolved,
        state_evolved_scattered,
    )


def get_random_boltzmann_state(
    system: PeriodicSystem,
    config: PeriodicSystemConfig,
    temperature: float,
) -> StateVector[Any]:
    """Generate a random Boltzmann state.

    Follows the formula described in eqn 5 in
    https://doi.org/10.48550/arXiv.2002.12035.


    Args:
    ----
        system (PeriodicSystem): system
        config (PeriodicSystemConfig): config
        temperature (float): temperature of the system

    Returns:
    -------
        StateVector[Any]: state with boltzmann distribution

    """
    hamiltonian = get_hamiltonian(system, config)
    boltzmann_distribution = np.exp(
        -hamiltonian["data"] / (2 * k * temperature),
    )

    random_phase = np.exp(2j * np.pi * np.random.rand(len(hamiltonian["data"])))
    normalization = np.sqrt(sum(np.square(boltzmann_distribution)))
    boltzmann_state = boltzmann_distribution * random_phase / normalization
    return {"basis": hamiltonian["basis"][0], "data": boltzmann_state}


def get_boltzmann_state(
    system: PeriodicSystem,
    config: PeriodicSystemConfig,
    temperature: float,
    phase: float,
) -> StateVector[Any]:
    hamiltonian = get_hamiltonian(system, config)
    boltzmann_distribution = np.exp(
        -hamiltonian["data"] / (2 * k * temperature),
    )

    normalization = np.sqrt(sum(np.square(boltzmann_distribution)))
    boltzmann_state = boltzmann_distribution * np.exp(1j * phase) / normalization
    return {"basis": hamiltonian["basis"][0], "data": boltzmann_state}


def get_average_boltzmann_isf(
    system: PeriodicSystem,
    config: PeriodicSystemConfig,
    times: _AX0Inv,
    direction: tuple[int] = (1,),
    temperature: float = 300,
    n: int = 10,
) -> ValueList[_AX0Inv]:
    isf_data = np.zeros(times.n, dtype=np.complex128)
    hamiltonian = get_hamiltonian(system, config)
    operator = get_periodic_x_operator(hamiltonian["basis"][0], direction)

    for _i in range(n):
        isf_data += get_isf_from_hamiltonian(
            hamiltonian,
            operator,
            get_random_boltzmann_state(system, config, temperature),
            times,
        )["data"]
    isf_data = isf_data / n
    return {
        "data": isf_data,
        "basis": times,
    }
