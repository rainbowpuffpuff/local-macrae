"""Classical MD (GROMACS, 128 TIP4P/2005 waters, 300 K) for Latin-hypercube charge sets of acetate."""
import argparse, subprocess
from bff.sampling import latin_hypercube
