#!/usr/bin/env python3
"""
Build script for portable hardware health checker executable.

Usage:
    python build_portable.py [--shader] [--build]

Options:
    --shader   Compile GLSL compute shader to SPIR-V (requires glslangValidator)
    --build    Build standalone executable with PyInstaller
"""

import argparse
import os
import subprocess
import sys


# Default SPIR-V bytecode for matrix multiplication compute shader
# This embedded SPIR-V is a placeholder - a real implementation
# would compile GLSL using glslangValidator at build time
SPIRV_PLACEHOLDER = bytes([
    # SPIR-V header (version 1.0)
    0x07, 0x23, 0x02, 0x00,  # Magic number
    0x01, 0x00, 0x00, 0x00,  # Version 1.0
    0x01, 0x00, 0x00, 0x00,  # Generator
    0x0c, 0x00, 0x00, 0x00,  # Bound
    0x00, 0x00, 0x00, 0x00,  # Schema
])


def compile_shader(shader_path: str = 'shaders/compute.comp',
                   spirv_path: str = 'shaders/compute.spv') -> None:
    """Compile GLSL compute shader to SPIR-V using glslangValidator.

    Requires glslangValidator to be installed and available on PATH.
    """
    if not os.path.exists(shader_path):
        print(f"Error: Shader file not found: {shader_path}")
        print("Please create the shader file first or skip this step.")
        sys.exit(1)

    try:
        result = subprocess.run(
            ['glslangValidator', '-V', shader_path, '-o', spirv_path],
            capture_output=True,
            text=True,
            check=True
        )
        print(f"Shader compiled successfully: {spirv_path}")
    except FileNotFoundError:
        print("Error: glslangValidator not found on PATH.")
        print("Install Vulkan SDK to compile GLSL shaders.")
        print(f"Using embedded SPIR-V placeholder instead.")
        os.makedirs(os.path.dirname(spirv_path) or '.', exist_ok=True)
        with open(spirv_path, 'wb') as f:
            f.write(SPIRV_PLACEHOLDER)
        print(f"Written placeholder SPIR-V to {spirv_path}")
    except subprocess.CalledProcessError as e:
        print(f"Error compiling shader: {e.stderr}")
        sys.exit(1)


def build_executable() -> None:
    """Build standalone executable using PyInstaller."""
    try:
        import PyInstaller.__main__
    except ImportError:
        print("Error: PyInstaller not installed.")
        print("Install with: pip install pyinstaller")
        sys.exit(1)

    if not os.path.exists('pyinstaller.spec'):
        print("Error: pyinstaller.spec not found.")
        sys.exit(1)

    print("Building standalone executable...")
    PyInstaller.__main__.run([
        'pyinstaller.spec',
        '--noconfirm',
        '--clean',
    ])

    exe_path = os.path.join('dist', 'hardware_health_checker.exe')
    if os.path.exists(exe_path):
        print(f"\nStandalone executable created: {exe_path}")
        print("\nTest it:")
        print(f"  {exe_path} --mode health")
        print(f"  {exe_path} --mode stress --duration quick --yes")
        print(f"  {exe_path} --mode both --duration standard --yes")
    else:
        print("Build may have failed - executable not found at expected path.")
        print("Check dist/ directory for output.")


def main():
    parser = argparse.ArgumentParser(
        description='Build portable hardware health checker executable'
    )
    parser.add_argument('--shader', action='store_true',
                        help='Compile GLSL compute shader to SPIR-V')
    parser.add_argument('--build', action='store_true',
                        help='Build standalone executable with PyInstaller')

    args = parser.parse_args()

    if not args.shader and not args.build:
        parser.print_help()
        sys.exit(0)

    if args.shader:
        compile_shader()

    if args.build:
        build_executable()


if __name__ == '__main__':
    main()