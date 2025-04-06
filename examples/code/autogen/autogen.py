import asyncio
import json
from dataclasses import dataclass
from typing import List, Dict, Any

from autogen_core import (
    AgentId, FunctionCall, MessageContext, RoutedAgent, 
    SingleThreadedAgentRuntime, message_handler, Agent
)
from autogen_core.models import (
    ChatCompletionClient, LLMMessage, SystemMessage,
    UserMessage, AssistantMessage, FunctionExecutionResult, FunctionExecutionResultMessage
)
from autogen_core.tools import FunctionTool, Tool
from autogen_ext.models.openai import OpenAIChatCompletionClient

import logging

logging.basicConfig(level=logging.WARNING)
logging.getLogger("autogen_core").setLevel(logging.DEBUG)


@dataclass
class Message:
    content: str

class BookingManager(RoutedAgent):
    """Manager agent that coordinates the travel booking process."""
    
    def __init__(self, model_client: ChatCompletionClient):
        super().__init__("Travel booking manager that routes requests to specialized agents")
        self._system_messages: List[LLMMessage] = [
            SystemMessage(content="You are a helpful travel booking manager. "
                        "Analyze user requests and route them to the appropriate specialized agent.")
        ]
        self._model_client = model_client
    
    @message_handler
    async def handle_user_message(self, message: Message, ctx: MessageContext) -> Message:
        session = self._system_messages + [
            UserMessage(content=message.content, source="user")
        ]
        
        create_result = await self._model_client.create(
            messages=session,
            cancellation_token=ctx.cancellation_token
        )
        
        # Routing logic based on query content
        query = message.content.lower()
        target_agent = None

        if "flight" in query or "airplane" in query or "travel" in query:
            target_agent = "flight_agent"
        elif "hotel" in query or "stay" in query or "accommodation" in query:
            target_agent = "hotel_agent"
        elif "car" in query or "rent" in query or "drive" in query:
            target_agent = "car_rental_agent"
        elif "price" in query or "cost" in query or "budget" in query:
            target_agent = "cost_calculator_agent"

        if target_agent:
            return await self._runtime.send_message(
                message,
                AgentId(target_agent, "default"),
            )


        # Fallback response if no routing keyword matched
        return Message(content=create_result.content)

class FlightAgent(RoutedAgent):
    """Agent specialized in flight bookings."""
    
    def __init__(self, model_client: ChatCompletionClient, tool_schema: List[Tool]):
        super().__init__("Flight booking specialist")
        self._system_messages: List[LLMMessage] = [
            SystemMessage(content="You are a flight booking specialist. Help users find flights.")
        ]
        self._model_client = model_client
        self._tools = tool_schema
    
    @message_handler
    async def handle_user_message(self, message: Message, ctx: MessageContext) -> Message:
        session = self._system_messages + [
            UserMessage(content=message.content, source="user")
        ]
        
        create_result = await self._model_client.create(
            messages=session,
            tools=self._tools,
            cancellation_token=ctx.cancellation_token
        )
        
        if isinstance(create_result.content, str):
            return Message(content=create_result.content)
        
        # Handle tool calls if any
        assert isinstance(create_result.content, list) and all(isinstance(call, FunctionCall) for call in create_result.content)
        
        session.append(AssistantMessage(content=create_result.content, source="assistant"))
        
        results = await asyncio.gather(*[
            self._execute_tool_call(call, ctx.cancellation_token) 
            for call in create_result.content
        ])
        
        session.append(FunctionExecutionResultMessage(content=results))
        
        final_result = await self._model_client.create(
            messages=session,
            cancellation_token=ctx.cancellation_token
        )
        
        assert isinstance(final_result.content, str)
        
        # If booking complete, can route back to manager
        if "booking complete" in final_result.content.lower():
            return Message(content="route:booking_manager")
        
        return Message(content=final_result.content)
    
    async def _execute_tool_call(self, call: FunctionCall, cancellation_token) -> FunctionExecutionResult:
        tool = next((tool for tool in self._tools if tool.name == call.name), None)
        assert tool is not None
        
        try:
            arguments = json.loads(call.arguments)
            result = await tool.run_json(arguments, cancellation_token)
            return FunctionExecutionResult(
                call_id=call.id,
                content=tool.return_value_as_string(result),
                is_error=False,
                name=tool.name
            )
        except Exception as e:
            return FunctionExecutionResult(
                call_id=call.id,
                content=str(e),
                is_error=True,
                name=tool.name
            )

class HotelAgent(RoutedAgent):
    """Agent specialized in hotel bookings."""
    
    def __init__(self, model_client: ChatCompletionClient, tool_schema: List[Tool]):
        super().__init__("Hotel booking specialist")
        self._system_messages: List[LLMMessage] = [
            SystemMessage(content="You are a hotel booking specialist. Help users find accommodations.")
        ]
        self._model_client = model_client
        self._tools = tool_schema
    
    @message_handler
    async def handle_user_message(self, message: Message, ctx: MessageContext) -> Message:
        # Similar implementation to FlightAgent
        session = self._system_messages + [
            UserMessage(content=message.content, source="user")
        ]
        
        create_result = await self._model_client.create(
            messages=session,
            tools=self._tools,
            cancellation_token=ctx.cancellation_token
        )
        
        # Simple implementation for brevity
        result = "I've found these hotel options for you. Would you like to proceed with booking?"
        
        # Route to cost calculator if price comparison needed
        if "price" in message.content.lower() or "compare" in message.content.lower():
            return Message(content="route:cost_calculator_agent")
            
        return Message(content=result)

class CarRentalAgent(RoutedAgent):
    """Agent specialized in car rentals."""
    
    def __init__(self, model_client: ChatCompletionClient, tool_schema: List[Tool]):
        super().__init__("Car rental specialist")
        self._system_messages: List[LLMMessage] = [
            SystemMessage(content="You are a car rental specialist. Help users find rental cars.")
        ]
        self._model_client = model_client
        self._tools = tool_schema
    
    @message_handler
    async def handle_user_message(self, message: Message, ctx: MessageContext) -> Message:
        # Simple implementation for brevity
        return Message(content="I can help you book a rental car. Here are some options...")

class CostCalculatorAgent(RoutedAgent):
    """Agent that calculates and compares travel costs."""
    
    def __init__(self, model_client: ChatCompletionClient, tool_schema: List[Tool]):
        super().__init__("Travel cost calculator")
        self._system_messages: List[LLMMessage] = [
            SystemMessage(content="You are a travel cost calculator. Help users compare prices.")
        ]
        self._model_client = model_client
        self._tools = tool_schema
    
    @message_handler
    async def handle_user_message(self, message: Message, ctx: MessageContext) -> Message:
        # Implementation would use pricing tools
        result = "Based on your requirements, here's the cost breakdown..."
        return Message(content=result)

# Define tool functions
async def search_flights(origin: str, destination: str, date: str) -> Dict[str, Any]:
    """Search for flights based on origin, destination, and date."""
    # In a real system, this would connect to a flight booking API
    return {
        "flights": [
            {"airline": "Delta", "flight_number": "DL123", "price": 450.0},
            {"airline": "United", "flight_number": "UA456", "price": 525.0}
        ]
    }

async def get_hotel_prices(location: str, check_in: str, check_out: str) -> Dict[str, Any]:
    """Get hotel prices for a specific location and dates."""
    # In a real system, this would connect to a hotel booking API
    return {
        "hotels": [
            {"name": "Grand Hotel", "price_per_night": 175.0},
            {"name": "Budget Inn", "price_per_night": 95.0}
        ]
    }

async def search_rental_cars(location: str, pickup_date: str, return_date: str) -> Dict[str, Any]:
    """Search for rental cars based on location and dates."""
    # In a real system, this would connect to a car rental API
    return {
        "cars": [
            {"type": "Economy", "provider": "Hertz", "price_per_day": 45.0},
            {"type": "SUV", "provider": "Enterprise", "price_per_day": 85.0}
        ]
    }

async def calculate_trip_cost(flights: List[Dict], hotels: List[Dict], cars: List[Dict]) -> Dict[str, Any]:
    """Calculate the total cost of a trip based on selected options."""
    # This would use actual selections in a real system
    flight_cost = flights[0]["price"] if flights else 0
    hotel_cost = hotels[0]["price_per_night"] * 3 if hotels else 0  # Assume 3 nights
    car_cost = cars[0]["price_per_day"] * 3 if cars else 0  # Assume 3 days
    
    total = flight_cost + hotel_cost + car_cost
    
    return {
        "flight_cost": flight_cost,
        "hotel_cost": hotel_cost,
        "car_cost": car_cost,
        "total_cost": total
    }

async def main():
    runtime = SingleThreadedAgentRuntime()
    model_client = OpenAIChatCompletionClient(model="gpt-4o-mini")

    # Define tools
    flight_tools = [FunctionTool(search_flights, description="Search for flights based on origin, destination, and date.")]
    hotel_tools = [FunctionTool(get_hotel_prices, description="Get hotel prices for a specific location and dates.")]
    car_tools = [FunctionTool(search_rental_cars, description="Search for rental cars based on location and dates.")]
    cost_tools = [FunctionTool(calculate_trip_cost, description="Calculate the total cost of a trip based on selected options.")]

    # Register agents
    await BookingManager.register(runtime, "booking_manager", lambda: BookingManager(model_client))
    await FlightAgent.register(runtime, "flight_agent", lambda: FlightAgent(model_client, flight_tools))
    await HotelAgent.register(runtime, "hotel_agent", lambda: HotelAgent(model_client, hotel_tools))
    await CarRentalAgent.register(runtime, "car_rental_agent", lambda: CarRentalAgent(model_client, car_tools))
    await CostCalculatorAgent.register(runtime, "cost_calculator_agent", lambda: CostCalculatorAgent(model_client, cost_tools))

    runtime.start()

    manager_id = AgentId("booking_manager", "default")

    await runtime.send_message(
        Message(content="I need to book a flight from New York to Los Angeles on June 15th."),
        manager_id,
    )

    await runtime.stop_when_idle()


# Run everything in a single main function
if __name__ == "__main__":
    asyncio.run(main())
