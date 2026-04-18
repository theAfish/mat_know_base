"""
Agent tools package.

Assembles all tools from sub-modules for agent registration.
"""

from mkb.agents.tools.reading import READING_TOOLS
from mkb.agents.tools.frames import FRAME_TOOLS

ALL_TOOLS = READING_TOOLS + FRAME_TOOLS
