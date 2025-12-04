# CadQuery Planetary Gear System
# A comprehensive library for generating parametric planetary gear systems

__version__ = "1.0.0"
__author__ = "Generated with CadQuery"

# Import main classes for easy access
from .spur_gear import SpurGear, HerringboneGear
from .Ring_gear import RingGear, HerringboneRingGear, PlanetaryGearset, HerringbonePlanetaryGearset
from .utilization import circle3d_by3points, rotation_matrix, make_shell

__all__ = [
    'SpurGear', 
    'HerringboneGear',
    'RingGear', 
    'HerringboneRingGear', 
    'PlanetaryGearset', 
    'HerringbonePlanetaryGearset',
    'circle3d_by3points',
    'rotation_matrix', 
    'make_shell'
]