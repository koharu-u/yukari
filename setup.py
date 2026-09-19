from setuptools import find_packages, setup


setup(
    name="yukari-42-tester",
    version="0.1.0",
    description="An original CLI test runner for 42 projects",
    packages=find_packages("src"),
    package_dir={"": "src"},
    python_requires=">=3.10",
    entry_points={"console_scripts": ["yukari=yukuri.cli:main", "42check=yukuri.cli:main"]},
)
