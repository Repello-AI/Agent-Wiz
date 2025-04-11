from __future__ import annotations as _annotations

import asyncio
import random
import uuid
from typing import Literal

from pydantic import BaseModel

from agents import (
    Agent,
    HandoffOutputItem,
    ItemHelpers,
    MessageOutputItem,
    RunContextWrapper,  
    Runner,
    ToolCallItem,
    ToolCallOutputItem,
    TResponseInputItem,
    function_tool,
    handoff,
    trace,
)
from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX


class EcommerceAgentContext(BaseModel):
    """Holds conversation state for e-commerce support."""
    customer_name: str | None = None
    order_id: str | None = None
    product_sku: str | None = None
    last_inquiry_type: Literal["order", "product", "other"] | None = None


@function_tool
async def get_order_status(order_id: str) -> str:
    """
    Looks up the status of a given order ID.

    Args:
        order_id: The unique identifier for the order (e.g., ORD-12345).
    """
    print(f"--- Tool: Simulating lookup for order: {order_id} ---")
    if not order_id or not order_id.startswith("ORD-"):
        return "Invalid order ID format. Please provide an ID like 'ORD-12345'."

    possible_statuses = ["Processing", "Shipped", "Delivered", "Delayed", "Cancelled"]
    status = possible_statuses[hash(order_id) % len(possible_statuses)]

    if status == "Shipped":
        tracking = f"TRK-{random.randint(100000000, 999999999)}"
        return f"Order {order_id} has been Shipped. Tracking number: {tracking}"
    elif status == "Delivered":
        return f"Order {order_id} was Delivered successfully."
    elif status == "Processing":
        return f"Order {order_id} is currently Processing. Expected ship date is in 2 business days."
    elif status == "Delayed":
        return f"Order {order_id} is currently Delayed due to high volume. We apologize for the inconvenience."
    else: # Cancelled
         return f"Order {order_id} has been Cancelled."

@function_tool
async def get_product_info(product_sku: str) -> str:
    """
    Provides information about a product based on its SKU.

    Args:
        product_sku: The Stock Keeping Unit (SKU) of the product (e.g., SKU-TECH-001).
    """
    print(f"--- Tool: Simulating lookup for product SKU: {product_sku} ---")
    if not product_sku or not product_sku.startswith("SKU-"):
         return "Invalid SKU format. Please provide an SKU like 'SKU-TECH-001'."

    products = {
        "SKU-TECH-001": {"name": "Wireless Mouse", "price": 25.99, "stock": 150, "desc": "A reliable ergonomic wireless mouse."},
        "SKU-TECH-002": {"name": "Mechanical Keyboard", "price": 79.99, "stock": 50, "desc": "A backlit mechanical keyboard with blue switches."},
        "SKU-HOME-001": {"name": "Coffee Mug", "price": 12.50, "stock": 0, "desc": "A ceramic coffee mug with our logo."},
    }

    info = products.get(product_sku)

    if info:
        stock_status = f"In Stock ({info['stock']} available)" if info['stock'] > 0 else "Out of Stock"
        return (
            f"Product: {info['name']} (SKU: {product_sku})\n"
            f"Description: {info['desc']}\n"
            f"Price: ${info['price']:.2f}\n"
            f"Availability: {stock_status}"
        )
    else:
        return f"Sorry, I could not find any information for product SKU: {product_sku}."


async def on_order_status_handoff(context: RunContextWrapper[EcommerceAgentContext]) -> None:
    print("--- Hook: Handing off to Order Status Agent ---")


triage_agent: Agent[EcommerceAgentContext]

order_status_agent = Agent[EcommerceAgentContext](
    name="Order Status Agent",
    handoff_description="Handles inquiries about the status of existing orders.",
    instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
    You are a specialized agent responsible for providing order status updates.
    Your goal is to assist customers who have questions about their orders.

    # Routine:
    1. Check if the user has provided an order ID. If not, politely ask for it (e.g., "Could you please provide your order ID, usually starting with 'ORD-'?").
    2. Once you have the order ID, use the `get_order_status` tool to look it up.
    3. Provide the status information clearly to the customer.
    4. If the customer asks about something *other* than order status (e.g., product details, returns, general questions), hand the conversation back to the Triage Agent. Do not attempt to answer unrelated questions yourself.
    """,
    tools=[get_order_status],
    handoffs=[], # Will be set after triage_agent is defined
)

product_info_agent = Agent[EcommerceAgentContext](
    name="Product Info Agent",
    handoff_description="Provides details about specific products based on their SKU.",
    instructions=f"""{RECOMMENDED_PROMPT_PREFIX}
    You are a specialized agent responsible for providing product information.
    Your goal is to assist customers looking for details about products we sell.

    # Routine:
    1. Check if the user has provided a product SKU. If not, politely ask for it (e.g., "Do you have the product SKU, usually starting with 'SKU-'?"). You can also try to infer it if they describe a product mentioned by the `get_product_info` tool.
    2. Once you have the SKU, use the `get_product_info` tool to look it up.
    3. Provide the product details (description, price, availability) clearly to the customer.
    4. If the customer asks about something *other* than product information (e.g., order status, returns, general questions), hand the conversation back to the Triage Agent. Do not attempt to answer unrelated questions yourself.
    """,
    tools=[get_product_info],
    handoffs=[], # Will be set after triage_agent is defined
)

triage_agent = Agent[EcommerceAgentContext](
    name="Triage Agent",
    handoff_description="The main customer support agent that directs inquiries to the correct specialist.",
    instructions=(
        f"{RECOMMENDED_PROMPT_PREFIX} "
        "You are the primary E-commerce Support Agent. Your main role is to understand the customer's needs and delegate the query to the appropriate specialist agent using your tools.\n"
        "Available Specialists:\n"
        "- **Order Status Agent:** Handles questions about existing order status.\n"
        "- **Product Info Agent:** Provides details about specific products.\n\n"
        "Analyze the customer's message. If it's clearly about an order status, hand off to the Order Status Agent. If it's clearly about product details, hand off to the Product Info Agent. If you are unsure, or it's a general question, try to clarify or answer briefly if possible, but prioritize handing off to specialists for their specific tasks."
        " If a specialist agent hands back to you, understand the context and see if another specialist is needed or if you can handle the request now."
    ),
    handoffs=[
        order_status_agent,
        product_info_agent,
        handoff(agent=order_status_agent, on_handoff=on_order_status_handoff),
    ],
)

order_status_agent.handoffs.append(triage_agent)
product_info_agent.handoffs.append(triage_agent)


async def main():
    current_agent: Agent[EcommerceAgentContext] = triage_agent
    input_items: list[TResponseInputItem] = []
    context = EcommerceAgentContext() # Initialize the context

    conversation_id = uuid.uuid4().hex[:16]
    print(f"Starting E-commerce Support Conversation (ID: {conversation_id})")
    print("Enter 'quit' to exit.")
    print(f"Agent: {current_agent.name}: How can I help you today?") # Initial greeting

    while True:
        user_input = input("You: ")
        if user_input.lower() == 'quit':
            print("Ending conversation.")
            break

        with trace("E-commerce Support Turn", group_id=conversation_id):
            input_items.append({"content": user_input, "role": "user"})

            result = await Runner.run(
                current_agent,
                input_items,
                context=context
            )

            for new_item in result.new_items:
                agent_name = new_item.agent.name
                if isinstance(new_item, MessageOutputItem):
                    message = ItemHelpers.text_message_output(new_item)
                    print(f"Agent: {agent_name}: {message}")
                elif isinstance(new_item, HandoffOutputItem):
                    print(
                        f"--- System: Handed off from {new_item.source_agent.name} to {new_item.target_agent.name} ---"
                    )
                    if new_item.target_agent is order_status_agent:
                         context.last_inquiry_type = "order"
                    elif new_item.target_agent is product_info_agent:
                         context.last_inquiry_type = "product"
                    else:
                         context.last_inquiry_type = "other"

                elif isinstance(new_item, ToolCallItem):
                    tool_name = new_item.tool_call.function.name
                    args = new_item.tool_call.function.arguments
                    print(f"--- System: {agent_name} calling tool `{tool_name}` with args: {args} ---")
                elif isinstance(new_item, ToolCallOutputItem):
                    pass # Often the agent summarizes this in its next message
                else:
                    print(f"--- System: {agent_name} produced item: {new_item.__class__.__name__} ---")

            input_items = result.to_input_list()
            current_agent = result.last_agent

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")
