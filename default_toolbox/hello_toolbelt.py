"""Example script for the toolbelt feature.

Assign this to a chat via the toolbox context menu, then run it
from the toolbelt panel to verify everything works.
"""
import sys

if __name__ == "__main__":
    chat_path = sys.argv[1] if len(sys.argv) > 1 else "(no path provided)"
    print(f"Hello from the toolbelt! Chat: {chat_path}")
