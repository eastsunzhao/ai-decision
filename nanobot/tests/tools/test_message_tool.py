import pytest

from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import OutboundMessage


@pytest.mark.asyncio
async def test_message_tool_returns_error_when_no_target_context(tmp_path) -> None:
    tool = MessageTool(workspace=tmp_path)
    result = await tool.execute(content="test")
    assert result == "Error: No target channel/chat specified"


@pytest.mark.asyncio
async def test_message_tool_marks_outbound_as_tool_delivery(tmp_path) -> None:
    sent: list[OutboundMessage] = []

    async def send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=send, workspace=tmp_path)
    tool.set_context("web", "10001:session")

    result = await tool.execute(content="hello")

    assert result == "Message sent to web:10001:session"
    assert sent[0].metadata["_message_tool_delivery"] is True
