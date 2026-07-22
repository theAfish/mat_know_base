import inspect

from mkb.agents.runtime import AgentRuntime, bind_tool


def test_bound_agent_tool_hides_and_uses_runtime_dependency():
    database = object()
    runtime = AgentRuntime(database=database)

    def operation(value: str, *, runtime: AgentRuntime) -> str:
        assert runtime.database is database
        return value.upper()

    tool = bind_tool(operation, runtime)

    assert list(inspect.signature(tool).parameters) == ["value"]
    assert tool("material") == "MATERIAL"
