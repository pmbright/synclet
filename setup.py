"""Setup script for Synclet."""

from setuptools import setup, find_packages

with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="synclet",
    version="0.1.0",
    description="Magento to QuickBooks Online sync tool",
    author="Your Name",
    packages=find_packages(),
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "synclet=src.synclet:main",
        ],
    },
    python_requires=">=3.8",
)
