import os
import sys
sys.path.insert(0, os.path.abspath('..'))  # чтобы autodoc видел tools/

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',   # поддержка Google/NumPy стиля docstrings
    'breathe',
    'exhale',                # опционально, для авто-иерархии C++
]

# Настройки Breathe
breathe_projects = {
    "Memcopy Bench": os.path.abspath("doxygen_out/xml")
}
breathe_default_project = "Memcopy Bench"

# Настройки Exhale (если используете)
exhale_args = {
    "containmentFolder":     "./api/cpp",
    "rootFileName":          "library_root.rst",
    "doxygenStripFromPath":  "..",
    "createTreeView":        True,
}

# Настройки autodoc
autodoc_default_options = {
    'members': True,
    'undoc-members': True,
    'show-inheritance': True,
}