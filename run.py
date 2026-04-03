#!/usr/bin/env python
"""Entry point for running ThreadBear CLI directly: python run.py"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import run

if __name__ == "__main__":
    run()
