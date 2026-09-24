"""Double-click this file to start the Rolimons Trade Bot."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trade_bot.app import main  # noqa: E402

sys.exit(main())
