"""Release entry point for installing Agent Toolkit."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / 'implementation/toolkit'))
from installer import main


if __name__ == '__main__':
    main()
