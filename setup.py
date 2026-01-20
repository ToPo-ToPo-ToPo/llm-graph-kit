
from setuptools import setup, find_packages
from pathlib import Path
this_directory = Path(__file__).parent

NAME = 'llm_graph'
AUTHOR = 'ToPo'
DESCRIPTION = "A graph library for large language models",
LONG_DESCRIPTION = (this_directory / "README.md").read_text(encoding="utf-8")
URL = 'https://github.com/ToPo-ToPo-ToPo/llm_graph'
LICENSE = 'Apache License Version 2.0'
VERSION = '1.0'
PYTHON_REQUIRES = ">=3.13.9"
CLASSIFIERS = [
    "Programming Language :: Python :: 3",
    "License :: OSI Approved :: Apache Software License",
]

# 自作パッケージに必要な他のパッケージ
INSTALL_REQUIRES = []

setup(
    name=NAME,
    author=AUTHOR,
    version=VERSION,
    description=DESCRIPTION,
    long_description=LONG_DESCRIPTION,
    long_description_content_type="text/markdown",
    url=URL,
    license=LICENSE,
    python_requires=PYTHON_REQUIRES,
    install_requires=INSTALL_REQUIRES,
    classifiers=CLASSIFIERS,
    packages=find_packages(),
)