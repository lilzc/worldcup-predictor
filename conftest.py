import os
import sys

# 让 tests/ 能 import 项目根模块（today / predict / config / src.*）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
