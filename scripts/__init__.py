"""HEADING OS engine scripts package.

The package marker that lets the wheel expose scripts.heading_cli:main as the
`heading` console entry point. Scripts are still invoked in-tree via sys.path
insertion; this only makes `scripts` an installable package.
"""
