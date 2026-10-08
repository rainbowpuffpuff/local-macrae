"""Regenerate the tiny synthetic PDFs in rag/tests/fixtures/ (text is invented; titles/DOIs match two real entries
of data/group_publications.json so metadata matching can be tested).

    python rag/tests/make_fixtures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

VESICLE = [
    # page 1: header, title, DOI, abstract
    "Soft Matter\n\n"
    "Vesicle internalization proceeds via a morphological phase transition\n\n"
    "I. Schachter, P. Jungwirth and D. Harries\n"
    "DOI: 10.1039/d6sm00560h\n\n"
    "Abstract\n"
    "Cells take up large cargo by wrapping it in membrane, yet how a vesicle crosses from partial adhesion to full "
    "internalization remains debated. Using simulations we show that internalization is a morphological phase "
    "transition of the wrapping membrane. Below a critical adhesion strength the vesicle stays partially wrapped; "
    "above it the membrane neck closes abruptly. The transition is controlled by the ratio of adhesion energy to "
    "bending rigidity, and line tension at the contact rim sets its sharpness. These findings connect membrane "
    "elasticity to the cooperative uptake of nanoparticles and viruses.",
    # page 2: methods
    "Methods\n"
    "All simulations used the coarse-grained Martini 3 force field with a time step of 20 fs. Membranes were "
    "composed of POPC and POPS in a 4:1 ratio, solvated with Martini water and 150 mM NaCl. Temperature was kept "
    "at 310 K with the velocity-rescaling thermostat and pressure at 1 bar with a semi-isotropic Parrinello-Rahman "
    "barostat. Each vesicle of 20 nm diameter was placed 2 nm above the bilayer. Adhesion strength was tuned by "
    "scaling the Lennard-Jones interaction between the vesicle surface beads and the lipid head groups. We ran "
    "twelve independent trajectories of 10 microseconds for each adhesion strength using GROMACS 2024. The wrapped "
    "fraction was computed from the contact area between vesicle and membrane, averaged over the last 2 "
    "microseconds of every trajectory. Bending rigidity was obtained from the undulation spectrum of a tension-free "
    "patch. Free energies of wrapping were estimated with umbrella sampling along the vesicle height coordinate.",
    # page 3: results, then the reference list (must not be indexed)
    "Results\n"
    "The wrapped fraction jumps from 0.4 to 1.0 within a narrow window of adhesion strength, a signature of a "
    "first-order morphological transition. Near the transition the membrane neck fluctuates between open and "
    "closed states, and the free energy profile shows two minima separated by a barrier of about 8 kT. Anionic "
    "POPS lipids accumulate at the contact rim and lower the line tension, which shifts the transition to weaker "
    "adhesion.\n\n"
    "References\n"
    "1 A. Author and B. Author, Zwitterionic quasicrystal membranes, J. Chem. Theory Comput., 2025, "
    "doi:10.1021/acs.jctc.5c02051\n"
    "2 C. Author, Hyperbolic origami of lipid sheets, Phys. Rev. Lett., 2024.\n",
]

GLYCAN = [
    # page 1: title split over lines with a hyphen break, no DOI anywhere -> matched by fuzzy title
    "Glycobiology, 2026\n\n"
    "Quantitative mapping of glycosaminoglycan sec-\nondary structure driven by sulfation and\niduronic acid\n\n"
    "M. Riopedre Fernández, D. Biriukov, H. Martinez-Seara\n\n"
    "Glycosaminoglycans such as heparin and heparan sulfate are long, highly charged polysaccharides whose shape "
    "governs their binding to proteins. We map their secondary structure with all-atom molecular dynamics and "
    "enhanced sampling. The ring puckering of iduronic acid switches between the chair and skew-boat "
    "conformations, and sulfation at the 2-O position stabilizes the skew-boat form. Calcium ions bridge "
    "neighbouring sulfate groups and stiffen the chain.",
    "Simulation details\n"
    "Chains of twelve disaccharide units were simulated with the GLYCAM06 force field and the electronic "
    "continuum correction for ions, using OPC water at 300 K. Ring puckering was described by Cremer-Pople "
    "coordinates and sampled with metadynamics. Persistence length was computed from the decay of bond "
    "vector correlations.",
]


def write_pdf(path: Path, pages: list[str], title: str = "", author: str = "") -> None:
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=420, height=595)
        rect = pymupdf.Rect(36, 36, 384, 559)
        left = page.insert_textbox(rect, text, fontsize=8, fontname="helv")
        if left < 0:
            raise ValueError(f"text does not fit on a page in {path.name}")
    doc.set_metadata({"title": title, "author": author, "producer": "rag test fixture"})
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, garbage=4, deflate=True)
    doc.close()


def main(out: Path = FIXTURES) -> None:
    # The vesicle PDF carries a typical publisher-style metadata title (a file name) to test that it's ignored.
    write_pdf(out / "vesicle.pdf", VESICLE, title="d6sm00560h.pdf")
    write_pdf(out / "glycan_paper.pdf", GLYCAN)
    print(f"wrote fixtures to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURES)
