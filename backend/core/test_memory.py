import os
import unittest
from unittest.mock import patch

from backend.core.memory import (
    checkpointer_backend,
    close_postgres_checkpointer,
    get_memory_saver,
    initialize_postgres_checkpointer,
)


class CheckpointerConfigTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await close_postgres_checkpointer()

    def test_memory_is_default_and_agent_savers_are_isolated(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(checkpointer_backend(), "memory")
            self.assertIsNot(get_memory_saver("qa"), get_memory_saver("exam"))

    async def test_memory_backend_does_not_import_postgres_driver(self):
        with patch.dict(os.environ, {"LANGGRAPH_CHECKPOINTER": "memory"}, clear=False):
            enabled = await initialize_postgres_checkpointer("postgresql://invalid")
            self.assertFalse(enabled)

    async def test_unknown_backend_is_rejected(self):
        with patch.dict(os.environ, {"LANGGRAPH_CHECKPOINTER": "redis"}, clear=False):
            with self.assertRaises(ValueError):
                await initialize_postgres_checkpointer("postgresql://invalid")


if __name__ == "__main__":
    unittest.main()
