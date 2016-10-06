# -*- coding: utf-8 -*-

"""Collection of physical constants and conversion factors.

The magnitudes of the defined constants are taken from
:module:`typhon.constants`.

This module adds units defined with pint's UnitRegistry..
"""
import numpy as np

from .common import ureg
from typhon import constants

# Physcial constants
g = earth_standard_gravity = constants.g * ureg('m / s**2')
h = planck = constants.planck * ureg.joule
k = boltzmann = constants.boltzmann * ureg('J / K')
c = speed_of_light = constants.speed_of_light * ureg('m / s')
N_A = avogadro = N = constants.avogadro * ureg('1 / mol')
R = gas_constant = constants.gas_constant * ureg('J * mol**-1 * K**-1')
molar_mass_dry_air = 28.9645e-3 * ureg('kg / mol')
molar_mass_water = 18.01528e-3 * ureg('kg / mol')
gas_constant_dry_air = R / molar_mass_dry_air  # J K^-1 kg^-1
gas_constant_water_vapor = R / molar_mass_water  # J K^-1 kg^-1

# Mathematical constants
golden = golden_ratio = (1 + np.sqrt(5)) / 2

# SI prefixes
yotta = 1e24
zetta = 1e21
exa = 1e18
peta = 1e15
tera = 1e12
giga = 1e9
mega = 1e6
kilo = 1e3
hecto = 1e2
deka = 1e1
deci = 1e-1
centi = 1e-2
milli = 1e-3
micro = 1e-6
nano = 1e-9
pico = 1e-12
femto = 1e-15
atto = 1e-18
zepto = 1e-21

# Non-SI ratios
ppm = 1e-6  # parts per million
ppb = 1e-9  # parts per billion
ppt = 1e-12  # parts per trillion

# Binary prefixes
kibi = KiB = 2**10
mebi = MiB = 2**20
gibi = 2**30
tebi = 2**40
pebi = 2**50
exbi = 2**60
zebi = 2**70
yobi = 2**80

KB = 10**3
MB = 10**6

# Earth characteristics
earth_mass = constants.earth_mass * ureg.kg
earth_radius = constants.earth_radius * ureg.m

# Miscellaneous
atm = atmosphere = constants.atm * ureg.pascal

# Deprecated constants from Wallace and Hobbs.
# Will be removed in a future version.
R_d = 287.0 * ureg('J / K / kg')
R_v = 461.51 * ureg('J / K / kg')
M_d = 28.97 * ureg('kg / kmol')
M_w = 18.016 * ureg('kg / kmol')
