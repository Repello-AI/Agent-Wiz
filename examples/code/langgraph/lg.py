#!/usr/bin/env python3

"""
Build a Customer Support Bot using LangGraph.

This script is a conversion of a Jupyter Notebook tutorial.
It demonstrates building a customer support bot for an airline
to help users research and make travel arrangements.
It covers LangGraph's interrupts, checkpointers, and state management
to organize tools and manage flight bookings, hotel reservations, etc.
"""

# === Prerequisites ===
# Ensure necessary packages are installed:
# pip install -U langgraph langchain-community langchain-anthropic tavily-python pandas openai

import getpass
import os
import shutil
import sqlite3
import re
import uuid
from datetime import date, datetime
from typing import Optional, Union, List, Dict, Annotated, Literal, Callable

import pandas as pd
import requests
import numpy as np
import openai
import pytz

from langchain_core.tools import tool
from langchain_core.messages import ToolMessage, AnyMessage, add_messages
from langchain_core.runnables import RunnableConfig, RunnableLambda, Runnable
from langchain_core.prompts import ChatPromptTemplate
from langchain_anthropic import ChatAnthropic
from langchain_community.tools.tavily_search import TavilySearchResults
# from langchain_openai import ChatOpenAI # Alternative LLM

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

from typing_extensions import TypedDict
from pydantic import BaseModel, Field
# from IPython.display import Image, display # For Jupyter visualization

# === Environment Setup ===

def _set_env(var: str):
    """Set environment variable if not already set."""
    if not os.environ.get(var):
        os.environ[var] = getpass.getpass(f"{var}: ")

print("Setting up API keys (if not already set in environment)...")
_set_env("ANTHROPIC_API_KEY")
_set_env("OPENAI_API_KEY") # Needed for embeddings, even if using Anthropic LLM
_set_env("TAVILY_API_KEY")

# === LangSmith Setup (Optional but Recommended) ===
# Set up LangSmith for observability and debugging.
# Sign up at https://smith.langchain.com and set API key environment variables:
# export LANGCHAIN_TRACING_V2="true"
# export LANGCHAIN_API_KEY="..."
# print("Consider setting up LangSmith for better observability.")

# === Database Setup ===

db_url = "https://storage.googleapis.com/benchmarks-artifacts/travel-db/travel2.sqlite"
local_file = "travel2.sqlite"
# The backup lets us restart for each tutorial section
backup_file = "travel2.backup.sqlite"
overwrite = False

print(f"Checking for database file: {local_file}")
if overwrite or not os.path.exists(local_file):
    print(f"Downloading database from {db_url}...")
    response = requests.get(db_url)
    response.raise_for_status()  # Ensure the request was successful
    with open(local_file, "wb") as f:
        f.write(response.content)
    print("Database downloaded.")
    # Backup - we will use this to "reset" our DB in each section
    shutil.copy(local_file, backup_file)
    print("Backup database created.")
else:
    print("Database file already exists.")
    if not os.path.exists(backup_file):
        shutil.copy(local_file, backup_file)
        print("Backup database created from existing file.")

def update_dates(file: str) -> str:
    """Convert the flight dates in the database to be relative to the present time."""
    print(f"Updating dates in database file: {file} using backup: {backup_file}")
    if not os.path.exists(backup_file):
        raise FileNotFoundError(f"Backup file {backup_file} not found. Cannot update dates.")
    shutil.copy(backup_file, file)
    conn = sqlite3.connect(file)
    cursor = conn.cursor()

    tables = pd.read_sql(
        "SELECT name FROM sqlite_master WHERE type='table';", conn
    ).name.tolist()
    tdf = {}
    for t in tables:
        tdf[t] = pd.read_sql(f"SELECT * from {t}", conn)

    example_time_str = tdf["flights"]["actual_departure"].replace("\\N", pd.NaT).max()
    if pd.isna(example_time_str):
        print("Warning: No valid 'actual_departure' times found to calculate time difference.")
        conn.close()
        return file

    try:
        # Try parsing with timezone first
        example_time = pd.to_datetime(example_time_str)
        if example_time.tz is None:
             # If no timezone, try localizing or assume a default like UTC
             example_time = pd.to_datetime(example_time_str).tz_localize('UTC') # Or use system's local timezone
    except Exception as e:
         print(f"Could not parse example time '{example_time_str}'. Error: {e}")
         conn.close()
         return file

    current_time = pd.to_datetime("now").tz_convert(example_time.tz) # Convert current time to example time's timezone
    time_diff = current_time - example_time
    print(f"Time difference calculated: {time_diff}")

    # Update bookings table
    tdf["bookings"]["book_date"] = (
        pd.to_datetime(tdf["bookings"]["book_date"].replace("\\N", pd.NaT), utc=True)
        + time_diff
    )

    # Update flights table
    datetime_columns = [
        "scheduled_departure",
        "scheduled_arrival",
        "actual_departure",
        "actual_arrival",
    ]
    for column in datetime_columns:
        # Ensure the column exists before attempting conversion
        if column in tdf["flights"].columns:
            tdf["flights"][column] = (
                pd.to_datetime(tdf["flights"][column].replace("\\N", pd.NaT)) + time_diff
            )
        else:
            print(f"Warning: Column '{column}' not found in flights table.")


    # Write updated data back to the database
    for table_name, df in tdf.items():
        try:
            df.to_sql(table_name, conn, if_exists="replace", index=False)
        except Exception as e:
            print(f"Error writing table {table_name}: {e}")

    # Clean up
    del df
    del tdf
    conn.commit()
    conn.close()
    print("Database dates updated.")
    return file

# Initialize the database with updated dates
db = update_dates(local_file)

# === Tools Definition ===

# --- Lookup Company Policies Tool ---
print("Setting up policy lookup tool...")
response = requests.get(
    "https://storage.googleapis.com/benchmarks-artifacts/travel-db/swiss_faq.md"
)
response.raise_for_status()
faq_text = response.text
docs = [{"page_content": txt} for txt in re.split(r"(?=\n##)", faq_text)]

class VectorStoreRetriever:
    """Simple vector store retriever using OpenAI embeddings and numpy."""
    def __init__(self, docs: list, vectors: list, oai_client):
        self._arr = np.array(vectors)
        self._docs = docs
        self._client = oai_client

    @classmethod
    def from_docs(cls, docs, oai_client):
        embeddings = oai_client.embeddings.create(
            model="text-embedding-3-small", input=[doc["page_content"] for doc in docs]
        )
        vectors = [emb.embedding for emb in embeddings.data]
        return cls(docs, vectors, oai_client)

    def query(self, query: str, k: int = 5) -> list[dict]:
        embed = self._client.embeddings.create(
            model="text-embedding-3-small", input=[query]
        )
        # "@" is matrix multiplication in python
        scores = np.array(embed.data[0].embedding) @ self._arr.T
        top_k_idx = np.argpartition(scores, -k)[-k:]
        top_k_idx_sorted = top_k_idx[np.argsort(-scores[top_k_idx])]
        return [
            {**self._docs[idx], "similarity": scores[idx]} for idx in top_k_idx_sorted
        ]

try:
    openai_client = openai.Client()
    retriever = VectorStoreRetriever.from_docs(docs, openai_client)
except Exception as e:
    print(f"Error initializing OpenAI client or VectorStoreRetriever: {e}")
    print("Policy lookup tool may not function correctly.")
    retriever = None # Set to None to indicate failure

@tool
def lookup_policy(query: str) -> str:
    """Consult the company policies to check whether certain options are permitted.
    Use this before making any flight changes performing other 'write' events."""
    if not retriever:
        return "Policy lookup tool is unavailable."
    try:
        docs_retrieved = retriever.query(query, k=2)
        return "\n\n".join([doc["page_content"] for doc in docs_retrieved])
    except Exception as e:
        return f"Error looking up policy: {e}"

# --- Flight Tools ---
print("Defining flight tools...")
@tool
def fetch_user_flight_information(config: RunnableConfig) -> list[dict]:
    """Fetch all tickets for the user along with corresponding flight information and seat assignments.

    Returns:
        A list of dictionaries where each dictionary contains the ticket details,
        associated flight details, and the seat assignments for each ticket belonging to the user.
    """
    configuration = config.get("configurable", {})
    passenger_id = configuration.get("passenger_id", None)
    if not passenger_id:
        raise ValueError("No passenger ID configured.")

    conn = sqlite3.connect(db)
    cursor = conn.cursor()

    query = """
    SELECT
        t.ticket_no, t.book_ref,
        f.flight_id, f.flight_no, f.departure_airport, f.arrival_airport, f.scheduled_departure, f.scheduled_arrival,
        bp.seat_no, tf.fare_conditions
    FROM
        tickets t
        JOIN ticket_flights tf ON t.ticket_no = tf.ticket_no
        JOIN flights f ON tf.flight_id = f.flight_id
        JOIN boarding_passes bp ON bp.ticket_no = t.ticket_no AND bp.flight_id = f.flight_id
    WHERE
        t.passenger_id = ?
    """
    try:
        cursor.execute(query, (passenger_id,))
        rows = cursor.fetchall()
        column_names = [column[0] for column in cursor.description]
        results = [dict(zip(column_names, row)) for row in rows]
    except sqlite3.Error as e:
        results = [{"error": f"Database error: {e}"}]
    finally:
        cursor.close()
        conn.close()

    return results

@tool
def search_flights(
    departure_airport: Optional[str] = None,
    arrival_airport: Optional[str] = None,
    start_time: Optional[Union[date, datetime]] = None,
    end_time: Optional[Union[date, datetime]] = None,
    limit: int = 20,
) -> list[dict]:
    """Search for flights based on departure airport, arrival airport, and departure time range."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()

    query = "SELECT * FROM flights WHERE 1 = 1"
    params = []

    if departure_airport:
        query += " AND departure_airport = ?"
        params.append(departure_airport)
    if arrival_airport:
        query += " AND arrival_airport = ?"
        params.append(arrival_airport)
    if start_time:
        query += " AND scheduled_departure >= ?"
        # Convert date/datetime to string format suitable for SQLite
        params.append(str(start_time))
    if end_time:
        query += " AND scheduled_departure <= ?"
        params.append(str(end_time))
    query += " LIMIT ?"
    params.append(limit)

    try:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        column_names = [column[0] for column in cursor.description]
        results = [dict(zip(column_names, row)) for row in rows]
    except sqlite3.Error as e:
        results = [{"error": f"Database error: {e}"}]
    finally:
        cursor.close()
        conn.close()

    return results

@tool
def update_ticket_to_new_flight(
    ticket_no: str, new_flight_id: int, *, config: RunnableConfig
) -> str:
    """Update the user's ticket to a new valid flight."""
    configuration = config.get("configurable", {})
    passenger_id = configuration.get("passenger_id", None)
    if not passenger_id:
        raise ValueError("No passenger ID configured.")

    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    result_message = ""

    try:
        # Validate new_flight_id and departure time constraint
        cursor.execute(
            "SELECT departure_airport, arrival_airport, scheduled_departure FROM flights WHERE flight_id = ?",
            (new_flight_id,),
        )
        new_flight = cursor.fetchone()
        if not new_flight:
            result_message = "Invalid new flight ID provided."
        else:
            column_names = [column[0] for column in cursor.description]
            new_flight_dict = dict(zip(column_names, new_flight))

            # Ensure scheduled_departure is timezone-aware for comparison
            try:
                departure_time_str = new_flight_dict["scheduled_departure"]
                departure_time = pd.to_datetime(departure_time_str)
                if departure_time.tzinfo is None:
                    # Attempt to localize or assume UTC if no timezone info
                    try:
                        # Try system's timezone first
                        departure_time = departure_time.tz_localize(datetime.now().astimezone().tzinfo)
                    except:
                        departure_time = departure_time.tz_localize('UTC') # Fallback to UTC
            except Exception as e:
                 return f"Error parsing departure time '{departure_time_str}': {e}"


            # Make current_time timezone-aware using the departure_time's timezone
            current_time = datetime.now(departure_time.tzinfo)

            time_until_seconds = (departure_time - current_time).total_seconds()

            # Allow rescheduling *up to* 3 hours before departure
            # (Original code was > 3 hours, this seems more likely for a booking)
            # Let's keep the original logic: time_until < (3 * 3600):
            if time_until_seconds < (3 * 3600):
                result_message = (
                    f"Not permitted to reschedule to a flight that is less than 3 hours "
                    f"from the current time. Selected flight is at {departure_time}."
                )
            else:
                # Check ownership and update
                cursor.execute(
                    "SELECT flight_id FROM ticket_flights WHERE ticket_no = ?", (ticket_no,)
                )
                current_flight = cursor.fetchone()
                if not current_flight:
                    result_message = "No existing ticket found for the given ticket number."
                else:
                    cursor.execute(
                        "SELECT ticket_no FROM tickets WHERE ticket_no = ? AND passenger_id = ?",
                        (ticket_no, passenger_id),
                    )
                    current_ticket = cursor.fetchone()
                    if not current_ticket:
                        result_message = f"Current signed-in passenger with ID {passenger_id} not the owner of ticket {ticket_no}"
                    else:
                        # Proceed with update
                        cursor.execute(
                            "UPDATE ticket_flights SET flight_id = ? WHERE ticket_no = ?",
                            (new_flight_id, ticket_no),
                        )
                        conn.commit()
                        result_message = "Ticket successfully updated to new flight."

    except sqlite3.Error as e:
        result_message = f"Database error during update: {e}"
        conn.rollback() # Rollback on error
    except Exception as e:
         result_message = f"An unexpected error occurred: {e}"
         try:
             conn.rollback()
         except: pass # Ignore rollback error if connection is already bad
    finally:
        cursor.close()
        conn.close()

    return result_message

@tool
def cancel_ticket(ticket_no: str, *, config: RunnableConfig) -> str:
    """Cancel the user's ticket and remove it from the database."""
    configuration = config.get("configurable", {})
    passenger_id = configuration.get("passenger_id", None)
    if not passenger_id:
        raise ValueError("No passenger ID configured.")
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    result_message = ""

    try:
        # Check if ticket exists
        cursor.execute(
            "SELECT flight_id FROM ticket_flights WHERE ticket_no = ?", (ticket_no,)
        )
        existing_ticket = cursor.fetchone()
        if not existing_ticket:
            result_message = "No existing ticket found for the given ticket number."
        else:
            # Check ownership
            cursor.execute(
                "SELECT ticket_no FROM tickets WHERE ticket_no = ? AND passenger_id = ?",
                (ticket_no, passenger_id),
            )
            current_ticket = cursor.fetchone()
            if not current_ticket:
                result_message = f"Current signed-in passenger with ID {passenger_id} not the owner of ticket {ticket_no}"
            else:
                # Proceed with cancellation (delete from ticket_flights, could also delete from tickets, boarding_passes etc.)
                # For simplicity, just deleting from ticket_flights as in the notebook
                cursor.execute("DELETE FROM ticket_flights WHERE ticket_no = ?", (ticket_no,))
                # Potentially delete from boarding_passes and tickets tables as well for full cancellation
                # cursor.execute("DELETE FROM boarding_passes WHERE ticket_no = ?", (ticket_no,))
                # cursor.execute("DELETE FROM tickets WHERE ticket_no = ?", (ticket_no,))
                conn.commit()
                result_message = "Ticket successfully cancelled."

    except sqlite3.Error as e:
        result_message = f"Database error during cancellation: {e}"
        conn.rollback()
    except Exception as e:
         result_message = f"An unexpected error occurred: {e}"
         try:
             conn.rollback()
         except: pass
    finally:
        cursor.close()
        conn.close()

    return result_message

# --- Car Rental Tools ---
print("Defining car rental tools...")
@tool
def search_car_rentals(
    location: Optional[str] = None,
    name: Optional[str] = None,
    price_tier: Optional[str] = None,
    start_date: Optional[Union[datetime, date]] = None,
    end_date: Optional[Union[datetime, date]] = None,
) -> list[dict]:
    """
    Search for car rentals based on location, name, price tier, start date, and end date.
    """
    conn = sqlite3.connect(db)
    cursor = conn.cursor()

    query = "SELECT * FROM car_rentals WHERE 1=1"
    params = []

    if location:
        query += " AND location LIKE ?"
        params.append(f"%{location}%")
    if name:
        query += " AND name LIKE ?"
        params.append(f"%{name}%")
    # Simplified search as in notebook: ignores dates and price tier for broader results
    # Add date/price filters if needed in a real application

    try:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        column_names = [column[0] for column in cursor.description]
        results = [dict(zip(column_names, row)) for row in rows]
    except sqlite3.Error as e:
        results = [{"error": f"Database error: {e}"}]
    finally:
        conn.close()

    return results

@tool
def book_car_rental(rental_id: int) -> str:
    """Book a car rental by its ID."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    result_message = ""
    try:
        cursor.execute("UPDATE car_rentals SET booked = 1 WHERE id = ?", (rental_id,))
        conn.commit()
        if cursor.rowcount > 0:
            result_message = f"Car rental {rental_id} successfully booked."
        else:
            result_message = f"No car rental found with ID {rental_id} or it was already booked."
    except sqlite3.Error as e:
        result_message = f"Database error: {e}"
        conn.rollback()
    finally:
        conn.close()
    return result_message

@tool
def update_car_rental(
    rental_id: int,
    start_date: Optional[Union[datetime, date]] = None,
    end_date: Optional[Union[datetime, date]] = None,
) -> str:
    """Update a car rental's start and end dates by its ID."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    updates = []
    params = []
    result_message = ""

    if start_date:
        updates.append("start_date = ?")
        params.append(str(start_date)) # Convert to string for DB
    if end_date:
        updates.append("end_date = ?")
        params.append(str(end_date)) # Convert to string for DB

    if not updates:
        return "No update information provided."

    params.append(rental_id)
    query = f"UPDATE car_rentals SET {', '.join(updates)} WHERE id = ?"

    try:
        cursor.execute(query, params)
        conn.commit()
        if cursor.rowcount > 0:
            result_message = f"Car rental {rental_id} successfully updated."
        else:
            result_message = f"No car rental found with ID {rental_id}."
    except sqlite3.Error as e:
        result_message = f"Database error: {e}"
        conn.rollback()
    finally:
        conn.close()
    return result_message

@tool
def cancel_car_rental(rental_id: int) -> str:
    """Cancel a car rental by its ID."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    result_message = ""
    try:
        # Set booked to 0 instead of deleting
        cursor.execute("UPDATE car_rentals SET booked = 0 WHERE id = ?", (rental_id,))
        conn.commit()
        if cursor.rowcount > 0:
            result_message = f"Car rental {rental_id} successfully cancelled."
        else:
            result_message = f"No car rental found with ID {rental_id} or it was already cancelled."
    except sqlite3.Error as e:
        result_message = f"Database error: {e}"
        conn.rollback()
    finally:
        conn.close()
    return result_message

# --- Hotel Tools ---
print("Defining hotel tools...")
@tool
def search_hotels(
    location: Optional[str] = None,
    name: Optional[str] = None,
    price_tier: Optional[str] = None,
    checkin_date: Optional[Union[datetime, date]] = None,
    checkout_date: Optional[Union[datetime, date]] = None,
) -> list[dict]:
    """
    Search for hotels based on location, name, price tier, check-in date, and check-out date.
    """
    conn = sqlite3.connect(db)
    cursor = conn.cursor()

    query = "SELECT * FROM hotels WHERE 1=1"
    params = []

    if location:
        query += " AND location LIKE ?"
        params.append(f"%{location}%")
    if name:
        query += " AND name LIKE ?"
        params.append(f"%{name}%")
    # Simplified search as in notebook: ignores dates and price tier

    try:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        column_names = [column[0] for column in cursor.description]
        results = [dict(zip(column_names, row)) for row in rows]
    except sqlite3.Error as e:
        results = [{"error": f"Database error: {e}"}]
    finally:
        conn.close()
    return results

@tool
def book_hotel(hotel_id: int) -> str:
    """Book a hotel by its ID."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    result_message = ""
    try:
        cursor.execute("UPDATE hotels SET booked = 1 WHERE id = ?", (hotel_id,))
        conn.commit()
        if cursor.rowcount > 0:
            result_message = f"Hotel {hotel_id} successfully booked."
        else:
            result_message = f"No hotel found with ID {hotel_id} or it was already booked."
    except sqlite3.Error as e:
        result_message = f"Database error: {e}"
        conn.rollback()
    finally:
        conn.close()
    return result_message

@tool
def update_hotel(
    hotel_id: int,
    checkin_date: Optional[Union[datetime, date]] = None,
    checkout_date: Optional[Union[datetime, date]] = None,
) -> str:
    """Update a hotel's check-in and check-out dates by its ID."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    updates = []
    params = []
    result_message = ""

    if checkin_date:
        updates.append("checkin_date = ?")
        params.append(str(checkin_date))
    if checkout_date:
        updates.append("checkout_date = ?")
        params.append(str(checkout_date))

    if not updates:
        return "No update information provided."

    params.append(hotel_id)
    query = f"UPDATE hotels SET {', '.join(updates)} WHERE id = ?"

    try:
        cursor.execute(query, params)
        conn.commit()
        if cursor.rowcount > 0:
            result_message = f"Hotel {hotel_id} successfully updated."
        else:
            result_message = f"No hotel found with ID {hotel_id}."
    except sqlite3.Error as e:
        result_message = f"Database error: {e}"
        conn.rollback()
    finally:
        conn.close()
    return result_message

@tool
def cancel_hotel(hotel_id: int) -> str:
    """Cancel a hotel by its ID."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    result_message = ""
    try:
        cursor.execute("UPDATE hotels SET booked = 0 WHERE id = ?", (hotel_id,))
        conn.commit()
        if cursor.rowcount > 0:
            result_message = f"Hotel {hotel_id} successfully cancelled."
        else:
            result_message = f"No hotel found with ID {hotel_id} or it was already cancelled."
    except sqlite3.Error as e:
        result_message = f"Database error: {e}"
        conn.rollback()
    finally:
        conn.close()
    return result_message

# --- Excursion Tools ---
print("Defining excursion tools...")
@tool
def search_trip_recommendations(
    location: Optional[str] = None,
    name: Optional[str] = None,
    keywords: Optional[str] = None,
) -> list[dict]:
    """Search for trip recommendations based on location, name, and keywords."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()

    query = "SELECT * FROM trip_recommendations WHERE 1=1"
    params = []

    if location:
        query += " AND location LIKE ?"
        params.append(f"%{location}%")
    if name:
        query += " AND name LIKE ?"
        params.append(f"%{name}%")
    if keywords:
        keyword_list = keywords.split(",")
        keyword_conditions = " OR ".join(["keywords LIKE ?" for _ in keyword_list])
        query += f" AND ({keyword_conditions})"
        params.extend([f"%{keyword.strip()}%" for keyword in keyword_list])

    try:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        column_names = [column[0] for column in cursor.description]
        results = [dict(zip(column_names, row)) for row in rows]
    except sqlite3.Error as e:
        results = [{"error": f"Database error: {e}"}]
    finally:
        conn.close()

    return results

@tool
def book_excursion(recommendation_id: int) -> str:
    """Book an excursion by its recommendation ID."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    result_message = ""
    try:
        cursor.execute(
            "UPDATE trip_recommendations SET booked = 1 WHERE id = ?", (recommendation_id,)
        )
        conn.commit()
        if cursor.rowcount > 0:
            result_message = f"Trip recommendation {recommendation_id} successfully booked."
        else:
            result_message = f"No trip recommendation found with ID {recommendation_id} or it was already booked."
    except sqlite3.Error as e:
        result_message = f"Database error: {e}"
        conn.rollback()
    finally:
        conn.close()
    return result_message

@tool
def update_excursion(recommendation_id: int, details: str) -> str:
    """Update a trip recommendation's details by its ID."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    result_message = ""
    try:
        cursor.execute(
            "UPDATE trip_recommendations SET details = ? WHERE id = ?",
            (details, recommendation_id),
        )
        conn.commit()
        if cursor.rowcount > 0:
            result_message = f"Trip recommendation {recommendation_id} successfully updated."
        else:
            result_message = f"No trip recommendation found with ID {recommendation_id}."
    except sqlite3.Error as e:
        result_message = f"Database error: {e}"
        conn.rollback()
    finally:
        conn.close()
    return result_message

@tool
def cancel_excursion(recommendation_id: int) -> str:
    """Cancel a trip recommendation by its ID."""
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    result_message = ""
    try:
        cursor.execute(
            "UPDATE trip_recommendations SET booked = 0 WHERE id = ?", (recommendation_id,)
        )
        conn.commit()
        if cursor.rowcount > 0:
            result_message = f"Trip recommendation {recommendation_id} successfully cancelled."
        else:
            result_message = f"No trip recommendation found with ID {recommendation_id} or it was already cancelled."
    except sqlite3.Error as e:
        result_message = f"Database error: {e}"
        conn.rollback()
    finally:
        conn.close()
    return result_message

# === Utilities ===
print("Defining utility functions...")
# General web search tool
tavily_tool = TavilySearchResults(max_results=1)

def handle_tool_error(state) -> dict:
    """Adds error messages to the state when a tool fails."""
    error = state.get("error")
    tool_calls = state["messages"][-1].tool_calls
    return {
        "messages": [
            ToolMessage(
                content=f"Error: {repr(error)}\n please fix your mistakes.",
                tool_call_id=tc["id"],
            )
            for tc in tool_calls
        ]
    }

def create_tool_node_with_fallback(tools: list) -> Runnable:
    """Creates a ToolNode with error handling fallback."""
    return ToolNode(tools).with_fallbacks(
        [RunnableLambda(handle_tool_error)], exception_key="error"
    )

def _print_event(event: dict, _printed: set, max_length=1500):
    """Prints events from the graph stream."""
    current_state = event.get("dialog_state")
    if current_state:
        print("Currently in:", current_state[-1])
    message = event.get("messages")
    if message:
        if isinstance(message, list):
            message = message[-1]
        if message.id not in _printed:
            # Use repr for non-HTML environments
            msg_repr = repr(message)
            # msg_repr = message.pretty_repr(html=True) # Use this in Jupyter/HTML environments
            if len(msg_repr) > max_length:
                msg_repr = msg_repr[:max_length] + " ... (truncated)"
            print(msg_repr)
            _printed.add(message.id)

# === LLM Definition ===
# Haiku is faster and cheaper, but less accurate
# llm = ChatAnthropic(model="claude-3-haiku-20240307")
llm = ChatAnthropic(model="claude-3-sonnet-20240229", temperature=1)
# You could swap LLMs, though you will likely want to update the prompts when doing so!
# llm = ChatOpenAI(model="gpt-4-turbo-preview")
print(f"Using LLM: {llm.model}")

# === Assistant Class Definition ===
class Assistant:
    """Represents an assistant that interacts with the LLM."""
    def __init__(self, runnable: Runnable):
        self.runnable = runnable

    def __call__(self, state: Dict, config: RunnableConfig):
        # The state structure might vary slightly depending on the part (e.g., Part 1 vs Part 2+)
        # This generic implementation tries to handle common patterns.
        while True:
            # Pass relevant state parts to the runnable
            current_state = {k: v for k, v in state.items() if k != 'error'} # Exclude 'error' key if present
            result = self.runnable.invoke(current_state)

            # If the LLM happens to return an empty response, re-prompt it
            empty_response = False
            if not result.tool_calls:
                if not result.content:
                    empty_response = True
                elif isinstance(result.content, list) and not result.content[0].get("text"):
                    empty_response = True

            if empty_response:
                messages = state["messages"] + [("user", "Respond with a real output.")]
                state = {**state, "messages": messages}
                # Re-assign current_state for the next invoke if needed, or modify state directly
                # Depending on how state is passed, you might need to update it here
            else:
                break
        return {"messages": result}

# ==============================================================================
# === Part 1: Zero-shot Agent ===
# ==============================================================================
print("\n=== Starting Part 1: Zero-shot Agent ===")

# --- Part 1 State ---
class StatePart1(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    # user_info is implicitly handled via config in this part

# --- Part 1 Assistant ---
primary_assistant_prompt_part1 = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a helpful customer support assistant for Swiss Airlines. "
            " Use the provided tools to search for flights, company policies, and other information to assist the user's queries. "
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            " If a search comes up empty, expand your search before giving up."
            "\n\nCurrent user:\n<User>\n{user_info}\n</User>" # user_info comes from config
            "\nCurrent time: {time}.",
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.now)

part_1_tools = [
    tavily_tool,
    fetch_user_flight_information,
    search_flights,
    lookup_policy,
    update_ticket_to_new_flight,
    cancel_ticket,
    search_car_rentals,
    book_car_rental,
    update_car_rental,
    cancel_car_rental,
    search_hotels,
    book_hotel,
    update_hotel,
    cancel_hotel,
    search_trip_recommendations,
    book_excursion,
    update_excursion,
    cancel_excursion,
]
part_1_assistant_runnable = primary_assistant_prompt_part1 | llm.bind_tools(part_1_tools)

# Need a specific Assistant class instance for Part 1's state handling
class AssistantPart1:
    def __init__(self, runnable: Runnable):
        self.runnable = runnable

    def __call__(self, state: StatePart1, config: RunnableConfig):
        while True:
            # Inject user_info from config into the state for the prompt
            configuration = config.get("configurable", {})
            passenger_id = configuration.get("passenger_id", None)
            current_state = {**state, "user_info": passenger_id}

            result = self.runnable.invoke(current_state)
            # If the LLM happens to return an empty response, re-prompt it
            empty_response = False
            if not result.tool_calls:
                if not result.content:
                    empty_response = True
                elif isinstance(result.content, list) and not result.content[0].get("text"):
                     empty_response = True

            if empty_response:
                messages = state["messages"] + [("user", "Respond with a real output.")]
                state = {**state, "messages": messages}
            else:
                break
        return {"messages": result}

# --- Part 1 Graph Definition ---
builder_part1 = StateGraph(StatePart1)
builder_part1.add_node("assistant", AssistantPart1(part_1_assistant_runnable))
builder_part1.add_node("tools", create_tool_node_with_fallback(part_1_tools))
builder_part1.add_edge(START, "assistant")
builder_part1.add_conditional_edges("assistant", tools_condition)
builder_part1.add_edge("tools", "assistant")

memory_part1 = MemorySaver()
part_1_graph = builder_part1.compile(checkpointer=memory_part1)

# --- Part 1 Graph Visualization (Commented out) ---
# try:
#     # display(Image(part_1_graph.get_graph(xray=True).draw_mermaid_png())) # Requires extra dependencies
#     part_1_graph.get_graph(xray=True).draw_mermaid_png(output_file_path="part1_graph.png")
#     print("Part 1 graph visualization saved to part1_graph.png")
# except Exception as e:
#     print(f"Could not generate Part 1 graph visualization: {e}")
#     pass

# --- Part 1 Example Conversation ---
print("\n--- Running Part 1 Example Conversation ---")

tutorial_questions = [
    "Hi there, what time is my flight?",
    "Am i allowed to update my flight to something sooner? I want to leave later today.",
    "Update my flight to sometime next week then",
    "The next available option is great",
    "what about lodging and transportation?",
    "Yeah i think i'd like an affordable hotel for my week-long stay (7 days). And I'll want to rent a car.",
    "OK could you place a reservation for your recommended hotel? It sounds nice.",
    "yes go ahead and book anything that's moderate expense and has availability.",
    "Now for a car, what are my options?",
    "Awesome let's just get the cheapest option. Go ahead and book for 7 days",
    "Cool so now what recommendations do you have on excursions?",
    "Are they available while I'm there?",
    "interesting - i like the museums, what options are there? ",
    "OK great pick one and book it for my second day there.",
]

# Reset DB for this part
db = update_dates(local_file)
thread_id_part1 = str(uuid.uuid4())

config_part1 = {
    "configurable": {
        "passenger_id": "3442 587242",
        "thread_id": thread_id_part1,
    }
}

_printed_part1 = set()
for i, question in enumerate(tutorial_questions):
    print(f"\n--- Part 1 - Turn {i+1} ---")
    print(f"User: {question}")
    events = part_1_graph.stream(
        {"messages": [("user", question)]}, config_part1, stream_mode="values"
    )
    for event in events:
        _print_event(event, _printed_part1)
    # No interrupts in Part 1, just continues

print("\n=== Part 1 Review ===")
print("Part 1 agent responded but made decisions without confirmation and struggled with some searches.")

# ==============================================================================
# === Part 2: Add Confirmation ===
# ==============================================================================
print("\n=== Starting Part 2: Add Confirmation ===")

# --- Part 2 State & Assistant ---
class StatePart2(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_info: str # Explicit user_info field

# Assistant class is reusable from the definition above

assistant_prompt_part2 = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a helpful customer support assistant for Swiss Airlines. "
            " Use the provided tools to search for flights, company policies, and other information to assist the user's queries. "
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            " If a search comes up empty, expand your search before giving up."
            "\n\nCurrent user:\n<User>\n{user_info}\n</User>" # user_info comes from state
            "\nCurrent time: {time}.",
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.now)

part_2_tools = part_1_tools # Reuse tools from Part 1
part_2_assistant_runnable = assistant_prompt_part2 | llm.bind_tools(part_2_tools)

# --- Part 2 Graph Definition ---
builder_part2 = StateGraph(StatePart2)

def user_info_node_part2(state: StatePart2, config: RunnableConfig):
    # Fetch user info using the config passed to the node
    passenger_id = config.get("configurable", {}).get("passenger_id")
    if not passenger_id:
         # Try fetching from existing state if available (might happen on resume)
         if state.get("user_info"):
              return {} # Already populated
         else:
             raise ValueError("Passenger ID not found in config for user_info_node")

    # Use the fetch_user_flight_information tool directly
    # Need to pass the config *again* to the tool
    fetched_info = fetch_user_flight_information.invoke({}, config=config)
    return {"user_info": str(fetched_info)} # Store as string or keep as list/dict

# Need to wrap the node function to pass the config correctly when invoked by the graph
def wrapped_user_info_node_part2(state: StatePart2, config: RunnableConfig):
     return user_info_node_part2(state, config)

builder_part2.add_node("fetch_user_info", wrapped_user_info_node_part2)
builder_part2.add_edge(START, "fetch_user_info")
builder_part2.add_node("assistant", Assistant(part_2_assistant_runnable))
builder_part2.add_node("tools", create_tool_node_with_fallback(part_2_tools))
builder_part2.add_edge("fetch_user_info", "assistant")
builder_part2.add_conditional_edges("assistant", tools_condition)
builder_part2.add_edge("tools", "assistant")

memory_part2 = MemorySaver()
part_2_graph = builder_part2.compile(
    checkpointer=memory_part2,
    interrupt_before=["tools"], # Interrupt before ANY tool use
)

# --- Part 2 Graph Visualization (Commented out) ---
# try:
#     # display(Image(part_2_graph.get_graph(xray=True).draw_mermaid_png()))
#     part_2_graph.get_graph(xray=True).draw_mermaid_png(output_file_path="part2_graph.png")
#     print("Part 2 graph visualization saved to part2_graph.png")
# except Exception as e:
#     print(f"Could not generate Part 2 graph visualization: {e}")
#     pass

# --- Part 2 Example Conversation ---
print("\n--- Running Part 2 Example Conversation ---")

# Reset DB for this part
db = update_dates(local_file)
thread_id_part2 = str(uuid.uuid4())

config_part2 = {
    "configurable": {
        "passenger_id": "3442 587242",
        "thread_id": thread_id_part2,
    }
}

_printed_part2 = set()
# Reuse tutorial questions
current_step_event = None # To hold the event data during interruption
for i, question in enumerate(tutorial_questions):
    print(f"\n--- Part 2 - Turn {i+1} ---")
    print(f"User: {question}")
    events = part_2_graph.stream(
        {"messages": [("user", question)]}, config_part2, stream_mode="values"
    )
    for event in events:
        _print_event(event, _printed_part2)
        # Store the last event that has messages, useful for getting tool_call_id during interrupt
        if "messages" in event:
             current_step_event = event

    snapshot = part_2_graph.get_state(config_part2)
    while snapshot and snapshot.next: # Check if snapshot is not None
        print("\n--- INTERRUPT ---")
        try:
            user_input = input(
                "Do you approve of the above actions? Type 'y' to continue; "
                "otherwise, explain your requested changes.\n\n"
            )
        except EOFError: # Handle non-interactive execution
            print("Non-interactive mode detected, defaulting to 'y'.")
            user_input = "y"

        if user_input.strip().lower() == "y":
            print("Continuing...")
            # If approved, continue the graph with None input
            events = part_2_graph.stream(None, config_part2, stream_mode="values")
        else:
            print("User disapproved or requested changes. Providing feedback...")
            # If disapproved, provide feedback as a ToolMessage
            if current_step_event and current_step_event["messages"][-1].tool_calls:
                 tool_call_id = current_step_event["messages"][-1].tool_calls[0]["id"]
                 tool_message = ToolMessage(
                     tool_call_id=tool_call_id,
                     content=f"API call denied by user. Reasoning: '{user_input}'. Continue assisting, accounting for the user's input.",
                 )
                 events = part_2_graph.stream({"messages": [tool_message]}, config_part2, stream_mode="values")
            else:
                 print("Error: Could not get tool_call_id for feedback. Continuing without feedback.")
                 events = part_2_graph.stream(None, config_part2, stream_mode="values")


        # Process subsequent events after resuming
        for event in events:
            _print_event(event, _printed_part2)
            if "messages" in event:
                 current_step_event = event

        # Update snapshot after resuming
        snapshot = part_2_graph.get_state(config_part2)

print("\n=== Part 2 Review ===")
print("Part 2 agent fetched user info automatically and allowed user confirmation before tool use.")

# ==============================================================================
# === Part 3: Conditional Interrupt ===
# ==============================================================================
print("\n=== Starting Part 3: Conditional Interrupt ===")

# --- Part 3 State & Assistant ---
StatePart3 = StatePart2 # Same state as Part 2
# Assistant class is reusable

assistant_prompt_part3 = assistant_prompt_part2 # Same prompt as Part 2

# Split tools into safe and sensitive
part_3_safe_tools = [
    tavily_tool,
    fetch_user_flight_information,
    search_flights,
    lookup_policy,
    search_car_rentals,
    search_hotels,
    search_trip_recommendations,
]
part_3_sensitive_tools = [
    update_ticket_to_new_flight,
    cancel_ticket,
    book_car_rental,
    update_car_rental,
    cancel_car_rental,
    book_hotel,
    update_hotel,
    cancel_hotel,
    book_excursion,
    update_excursion,
    cancel_excursion,
]
sensitive_tool_names = {t.name for t in part_3_sensitive_tools}

# Bind all tools to the LLM
part_3_assistant_runnable = assistant_prompt_part3 | llm.bind_tools(
    part_3_safe_tools + part_3_sensitive_tools
)

# --- Part 3 Graph Definition ---
builder_part3 = StateGraph(StatePart3)

# Re-wrap the user_info node function for Part 3 config/state
def wrapped_user_info_node_part3(state: StatePart3, config: RunnableConfig):
     return user_info_node_part2(state, config) # Use Part 2's logic

builder_part3.add_node("fetch_user_info", wrapped_user_info_node_part3)
builder_part3.add_edge(START, "fetch_user_info")
builder_part3.add_node("assistant", Assistant(part_3_assistant_runnable))
builder_part3.add_node("safe_tools", create_tool_node_with_fallback(part_3_safe_tools))
builder_part3.add_node(
    "sensitive_tools", create_tool_node_with_fallback(part_3_sensitive_tools)
)
builder_part3.add_edge("fetch_user_info", "assistant")

def route_tools_part3(state: StatePart3) -> Literal["safe_tools", "sensitive_tools", END]:
    """Routes to safe or sensitive tool node based on the tool called."""
    next_node = tools_condition(state)
    if next_node == END:
        return END
    # Assumes single tool call for simplicity, like notebook
    ai_message = state["messages"][-1]
    if not ai_message.tool_calls:
         return END # Should not happen if tools_condition didn't return END, but safeguard
    first_tool_call = ai_message.tool_calls[0]
    if first_tool_call["name"] in sensitive_tool_names:
        return "sensitive_tools"
    return "safe_tools"

builder_part3.add_conditional_edges(
    "assistant", route_tools_part3, ["safe_tools", "sensitive_tools", END]
)
builder_part3.add_edge("safe_tools", "assistant")
builder_part3.add_edge("sensitive_tools", "assistant")

memory_part3 = MemorySaver()
part_3_graph = builder_part3.compile(
    checkpointer=memory_part3,
    interrupt_before=["sensitive_tools"], # Interrupt only before sensitive tools
)

# --- Part 3 Graph Visualization (Commented out) ---
# try:
#     # display(Image(part_3_graph.get_graph(xray=True).draw_mermaid_png()))
#     part_3_graph.get_graph(xray=True).draw_mermaid_png(output_file_path="part3_graph.png")
#     print("Part 3 graph visualization saved to part3_graph.png")
# except Exception as e:
#     print(f"Could not generate Part 3 graph visualization: {e}")
#     pass

# --- Part 3 Example Conversation ---
print("\n--- Running Part 3 Example Conversation ---")

# Reset DB for this part
db = update_dates(local_file)
thread_id_part3 = str(uuid.uuid4())

config_part3 = {
    "configurable": {
        "passenger_id": "3442 587242",
        "thread_id": thread_id_part3,
    }
}

_printed_part3 = set()
current_step_event_part3 = None
# Reuse tutorial questions
for i, question in enumerate(tutorial_questions):
    print(f"\n--- Part 3 - Turn {i+1} ---")
    print(f"User: {question}")
    events = part_3_graph.stream(
        {"messages": [("user", question)]}, config_part3, stream_mode="values"
    )
    for event in events:
        _print_event(event, _printed_part3)
        if "messages" in event:
             current_step_event_part3 = event

    snapshot = part_3_graph.get_state(config_part3)
    while snapshot and snapshot.next: # Check if snapshot is not None
        # Handle interrupt logic (same as Part 2)
        print("\n--- INTERRUPT (Sensitive Tool) ---")
        try:
            user_input = input(
                "Do you approve of the above actions? Type 'y' to continue; "
                "otherwise, explain your requested changes.\n\n"
            )
        except EOFError:
            print("Non-interactive mode detected, defaulting to 'y'.")
            user_input = "y"

        if user_input.strip().lower() == "y":
            print("Continuing...")
            events = part_3_graph.stream(None, config_part3, stream_mode="values")
        else:
            print("User disapproved or requested changes. Providing feedback...")
            if current_step_event_part3 and current_step_event_part3["messages"][-1].tool_calls:
                 tool_call_id = current_step_event_part3["messages"][-1].tool_calls[0]["id"]
                 tool_message = ToolMessage(
                     tool_call_id=tool_call_id,
                     content=f"API call denied by user. Reasoning: '{user_input}'. Continue assisting, accounting for the user's input.",
                 )
                 events = part_3_graph.stream({"messages": [tool_message]}, config_part3, stream_mode="values")
            else:
                 print("Error: Could not get tool_call_id for feedback. Continuing without feedback.")
                 events = part_3_graph.stream(None, config_part3, stream_mode="values")


        for event in events:
            _print_event(event, _printed_part3)
            if "messages" in event:
                 current_step_event_part3 = event
        snapshot = part_3_graph.get_state(config_part3)


print("\n=== Part 3 Review ===")
print("Part 3 agent reduced interruptions by confirming only sensitive actions.")

# ==============================================================================
# === Part 4: Specialized Workflows ===
# ==============================================================================
print("\n=== Starting Part 4: Specialized Workflows ===")

# --- Part 4 State ---
def update_dialog_stack(left: list[str], right: Optional[str]) -> list[str]:
    """Push or pop the state."""
    if right is None:
        return left if left is not None else [] # Ensure left is not None
    if right == "pop":
        return left[:-1] if left else [] # Handle popping from empty list
    return (left if left is not None else []) + [right]

class StatePart4(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    user_info: str
    # Keep track of the current workflow/assistant
    dialog_state: Annotated[
        list[
            Literal[
                "assistant", # Primary assistant
                "update_flight",
                "book_car_rental",
                "book_hotel",
                "book_excursion",
            ]
        ],
        update_dialog_stack,
    ]

# --- Part 4 Assistants ---

# Base Assistant class is reusable

class CompleteOrEscalate(BaseModel):
    """A tool to mark the current task as completed and/or to escalate control of the dialog to the main assistant."""
    cancel: bool = True
    reason: str

    class Config:
        json_schema_extra = {
            "example": { "cancel": True, "reason": "User changed their mind about the current task." },
            "example 2": { "cancel": True, "reason": "I have fully completed the task." },
            "example 3": { "cancel": False, "reason": "I need to search the user's emails or calendar for more information." },
        }

# Flight booking assistant runnable
flight_booking_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a specialized assistant for handling flight updates. "
            " The primary assistant delegates work to you whenever the user needs help updating their bookings. "
            "Confirm the updated flight details with the customer and inform them of any additional fees. "
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            "If you need more information or the customer changes their mind, escalate the task back to the main assistant."
            " Remember that a booking isn't completed until after the relevant tool has successfully been used."
            "\n\nCurrent user flight information:\n<Flights>\n{user_info}\n</Flights>"
            "\nCurrent time: {time}."
            "\n\nIf the user needs help, and none of your tools are appropriate for it, then"
            ' "CompleteOrEscalate" the dialog to the host assistant. Do not waste the user\'s time. Do not make up invalid tools or functions.',
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.now)
update_flight_safe_tools = [search_flights]
update_flight_sensitive_tools = [update_ticket_to_new_flight, cancel_ticket]
update_flight_tools = update_flight_safe_tools + update_flight_sensitive_tools
update_flight_runnable = flight_booking_prompt | llm.bind_tools(
    update_flight_tools + [CompleteOrEscalate]
)

# Hotel Booking Assistant runnable
book_hotel_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a specialized assistant for handling hotel bookings. "
            "The primary assistant delegates work to you whenever the user needs help booking a hotel. "
            "Search for available hotels based on the user's preferences and confirm the booking details with the customer. "
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            "If you need more information or the customer changes their mind, escalate the task back to the main assistant."
            " Remember that a booking isn't completed until after the relevant tool has successfully been used."
            "\nCurrent time: {time}."
            '\n\nIf the user needs help, and none of your tools are appropriate for it, then "CompleteOrEscalate" the dialog to the host assistant.'
            " Do not waste the user's time. Do not make up invalid tools or functions."
            "\n\nSome examples for which you should CompleteOrEscalate:\n"
            " - 'what's the weather like this time of year?'\n"
            " - 'nevermind i think I'll book separately'\n"
            " - 'i need to figure out transportation while i'm there'\n"
            " - 'Oh wait i haven't booked my flight yet i'll do that first'\n"
            " - 'Hotel booking confirmed'",
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.now)
book_hotel_safe_tools = [search_hotels]
book_hotel_sensitive_tools = [book_hotel, update_hotel, cancel_hotel]
book_hotel_tools = book_hotel_safe_tools + book_hotel_sensitive_tools
book_hotel_runnable = book_hotel_prompt | llm.bind_tools(
    book_hotel_tools + [CompleteOrEscalate]
)

# Car Rental Assistant runnable
book_car_rental_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a specialized assistant for handling car rental bookings. "
            "The primary assistant delegates work to you whenever the user needs help booking a car rental. "
            "Search for available car rentals based on the user's preferences and confirm the booking details with the customer. "
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            "If you need more information or the customer changes their mind, escalate the task back to the main assistant."
            " Remember that a booking isn't completed until after the relevant tool has successfully been used."
            "\nCurrent time: {time}."
            "\n\nIf the user needs help, and none of your tools are appropriate for it, then "
            '"CompleteOrEscalate" the dialog to the host assistant. Do not waste the user\'s time. Do not make up invalid tools or functions.'
            "\n\nSome examples for which you should CompleteOrEscalate:\n"
            " - 'what's the weather like this time of year?'\n"
            " - 'What flights are available?'\n"
            " - 'nevermind i think I'll book separately'\n"
            " - 'Oh wait i haven't booked my flight yet i'll do that first'\n"
            " - 'Car rental booking confirmed'",
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.now)
book_car_rental_safe_tools = [search_car_rentals]
book_car_rental_sensitive_tools = [book_car_rental, update_car_rental, cancel_car_rental]
book_car_rental_tools = book_car_rental_safe_tools + book_car_rental_sensitive_tools
book_car_rental_runnable = book_car_rental_prompt | llm.bind_tools(
    book_car_rental_tools + [CompleteOrEscalate]
)

# Excursion Assistant runnable
book_excursion_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a specialized assistant for handling trip recommendations. "
            "The primary assistant delegates work to you whenever the user needs help booking a recommended trip. "
            "Search for available trip recommendations based on the user's preferences and confirm the booking details with the customer. "
            "If you need more information or the customer changes their mind, escalate the task back to the main assistant."
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            " Remember that a booking isn't completed until after the relevant tool has successfully been used."
            "\nCurrent time: {time}."
            '\n\nIf the user needs help, and none of your tools are appropriate for it, then "CompleteOrEscalate" the dialog to the host assistant. Do not waste the user\'s time. Do not make up invalid tools or functions.'
            "\n\nSome examples for which you should CompleteOrEscalate:\n"
            " - 'nevermind i think I'll book separately'\n"
            " - 'i need to figure out transportation while i'm there'\n"
            " - 'Oh wait i haven't booked my flight yet i'll do that first'\n"
            " - 'Excursion booking confirmed!'",
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.now)
book_excursion_safe_tools = [search_trip_recommendations]
book_excursion_sensitive_tools = [book_excursion, update_excursion, cancel_excursion]
book_excursion_tools = book_excursion_safe_tools + book_excursion_sensitive_tools
book_excursion_runnable = book_excursion_prompt | llm.bind_tools(
    book_excursion_tools + [CompleteOrEscalate]
)

# Primary Assistant (Router/Supervisor)
class ToFlightBookingAssistant(BaseModel):
    """Transfers work to a specialized assistant to handle flight updates and cancellations."""
    request: str = Field(description="Any necessary followup questions the update flight assistant should clarify before proceeding.")

class ToBookCarRental(BaseModel):
    """Transfers work to a specialized assistant to handle car rental bookings."""
    location: str = Field(description="The location where the user wants to rent a car.")
    start_date: str = Field(description="The start date of the car rental.")
    end_date: str = Field(description="The end date of the car rental.")
    request: str = Field(description="Any additional information or requests from the user regarding the car rental.")
    class Config:
        json_schema_extra = {"example": {"location": "Basel", "start_date": "2023-07-01", "end_date": "2023-07-05", "request": "I need a compact car with automatic transmission."}}

class ToHotelBookingAssistant(BaseModel):
    """Transfer work to a specialized assistant to handle hotel bookings."""
    location: str = Field(description="The location where the user wants to book a hotel.")
    checkin_date: str = Field(description="The check-in date for the hotel.")
    checkout_date: str = Field(description="The check-out date for the hotel.")
    request: str = Field(description="Any additional information or requests from the user regarding the hotel booking.")
    class Config:
        json_schema_extra = {"example": {"location": "Zurich", "checkin_date": "2023-08-15", "checkout_date": "2023-08-20", "request": "I prefer a hotel near the city center with a room that has a view."}}

class ToBookExcursion(BaseModel):
    """Transfers work to a specialized assistant to handle trip recommendation and other excursion bookings."""
    location: str = Field(description="The location where the user wants to book a recommended trip.")
    request: str = Field(description="Any additional information or requests from the user regarding the trip recommendation.")
    class Config:
        json_schema_extra = {"example": {"location": "Lucerne", "request": "The user is interested in outdoor activities and scenic views."}}


primary_assistant_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a helpful customer support assistant for Swiss Airlines. "
            "Your primary role is to search for flight information and company policies to answer customer queries. "
            "If a customer requests to update or cancel a flight, book a car rental, book a hotel, or get trip recommendations, "
            "delegate the task to the appropriate specialized assistant by invoking the corresponding tool. You are not able to make these types of changes yourself."
            " Only the specialized assistants are given permission to do this for the user."
            "The user is not aware of the different specialized assistants, so do not mention them; just quietly delegate through function calls. "
            "Provide detailed information to the customer, and always double-check the database before concluding that information is unavailable. "
            " When searching, be persistent. Expand your query bounds if the first search returns no results. "
            " If a search comes up empty, expand your search before giving up."
            "\n\nCurrent user flight information:\n<Flights>\n{user_info}\n</Flights>"
            "\nCurrent time: {time}.",
        ),
        ("placeholder", "{messages}"),
    ]
).partial(time=datetime.now)
primary_assistant_tools = [
    tavily_tool,
    search_flights,
    lookup_policy,
]
assistant_runnable = primary_assistant_prompt | llm.bind_tools(
    primary_assistant_tools +
    [ToFlightBookingAssistant, ToBookCarRental, ToHotelBookingAssistant, ToBookExcursion]
)

# --- Part 4 Utility ---
def create_entry_node(assistant_name: str, new_dialog_state: str) -> Callable:
    """Creates a node that signals entry into a specialized workflow."""
    def entry_node(state: StatePart4) -> dict:
        tool_call_id = state["messages"][-1].tool_calls[0]["id"]
        return {
            "messages": [
                ToolMessage(
                    content=f"The assistant is now the {assistant_name}. Reflect on the above conversation between the host assistant and the user."
                            f" The user's intent is unsatisfied. Use the provided tools to assist the user. Remember, you are {assistant_name},"
                            " and the booking, update, other other action is not complete until after you have successfully invoked the appropriate tool."
                            " If the user changes their mind or needs help for other tasks, call the CompleteOrEscalate function to let the primary host assistant take control."
                            " Do not mention who you are - just act as the proxy for the assistant.",
                    tool_call_id=tool_call_id,
                )
            ],
            "dialog_state": new_dialog_state, # Push the new state onto the stack
        }
    return entry_node

# --- Part 4 Graph Definition ---
builder_part4 = StateGraph(StatePart4)

# Re-wrap the user_info node function for Part 4 config/state
def wrapped_user_info_node_part4(state: StatePart4, config: RunnableConfig):
     # Add dialog_state initialization if it's the very first call
     initial_state = {"dialog_state": "assistant"} if not state.get("dialog_state") else {}
     user_info_update = user_info_node_part2(state, config) # Use Part 2's logic
     return {**initial_state, **user_info_update}


builder_part4.add_node("fetch_user_info", wrapped_user_info_node_part4)
builder_part4.add_edge(START, "fetch_user_info")

# --- Flight Booking Workflow Nodes ---
builder_part4.add_node(
    "enter_update_flight",
    create_entry_node("Flight Updates & Booking Assistant", "update_flight"),
)
builder_part4.add_node("update_flight", Assistant(update_flight_runnable))
builder_part4.add_edge("enter_update_flight", "update_flight")
builder_part4.add_node(
    "update_flight_sensitive_tools",
    create_tool_node_with_fallback(update_flight_sensitive_tools),
)
builder_part4.add_node(
    "update_flight_safe_tools",
    create_tool_node_with_fallback(update_flight_safe_tools),
)

def route_update_flight(state: StatePart4) -> Literal["update_flight_sensitive_tools", "update_flight_safe_tools", "leave_skill", END]:
    route = tools_condition(state)
    if route == END: return END
    tool_calls = state["messages"][-1].tool_calls
    if not tool_calls: return END # Safeguard
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel: return "leave_skill"
    safe_toolnames = [t.name for t in update_flight_safe_tools]
    if all(tc["name"] in safe_toolnames for tc in tool_calls): return "update_flight_safe_tools"
    return "update_flight_sensitive_tools"

builder_part4.add_edge("update_flight_sensitive_tools", "update_flight")
builder_part4.add_edge("update_flight_safe_tools", "update_flight")
builder_part4.add_conditional_edges(
    "update_flight", route_update_flight,
    ["update_flight_sensitive_tools", "update_flight_safe_tools", "leave_skill", END]
)

# Shared exit node
def pop_dialog_state(state: StatePart4) -> dict:
    """Pops the dialog stack and returns to the main assistant."""
    messages = []
    # Check if the last message has tool calls before trying to access them
    last_message = state["messages"][-1] if state["messages"] else None
    if last_message and last_message.tool_calls:
        # Note: Doesn't handle parallel tool calls
        tool_call_id = last_message.tool_calls[0]["id"]
        messages.append(
            ToolMessage(
                content="Resuming dialog with the host assistant. Please reflect on the past conversation and assist the user as needed.",
                tool_call_id=tool_call_id,
            )
        )
    return { "dialog_state": "pop", "messages": messages } # Signal to pop

builder_part4.add_node("leave_skill", pop_dialog_state)
builder_part4.add_edge("leave_skill", "primary_assistant") # Return to primary assistant

# --- Car Rental Workflow Nodes ---
builder_part4.add_node(
    "enter_book_car_rental",
    create_entry_node("Car Rental Assistant", "book_car_rental"),
)
builder_part4.add_node("book_car_rental", Assistant(book_car_rental_runnable))
builder_part4.add_edge("enter_book_car_rental", "book_car_rental")
builder_part4.add_node(
    "book_car_rental_safe_tools", create_tool_node_with_fallback(book_car_rental_safe_tools)
)
builder_part4.add_node(
    "book_car_rental_sensitive_tools", create_tool_node_with_fallback(book_car_rental_sensitive_tools)
)

def route_book_car_rental(state: StatePart4) -> Literal["book_car_rental_sensitive_tools", "book_car_rental_safe_tools", "leave_skill", END]:
    route = tools_condition(state)
    if route == END: return END
    tool_calls = state["messages"][-1].tool_calls
    if not tool_calls: return END
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel: return "leave_skill"
    safe_toolnames = [t.name for t in book_car_rental_safe_tools]
    if all(tc["name"] in safe_toolnames for tc in tool_calls): return "book_car_rental_safe_tools"
    return "book_car_rental_sensitive_tools"

builder_part4.add_edge("book_car_rental_sensitive_tools", "book_car_rental")
builder_part4.add_edge("book_car_rental_safe_tools", "book_car_rental")
builder_part4.add_conditional_edges(
    "book_car_rental", route_book_car_rental,
    ["book_car_rental_safe_tools", "book_car_rental_sensitive_tools", "leave_skill", END]
)

# --- Hotel Booking Workflow Nodes ---
builder_part4.add_node(
    "enter_book_hotel", create_entry_node("Hotel Booking Assistant", "book_hotel")
)
builder_part4.add_node("book_hotel", Assistant(book_hotel_runnable))
builder_part4.add_edge("enter_book_hotel", "book_hotel")
builder_part4.add_node("book_hotel_safe_tools", create_tool_node_with_fallback(book_hotel_safe_tools))
builder_part4.add_node("book_hotel_sensitive_tools", create_tool_node_with_fallback(book_hotel_sensitive_tools))

def route_book_hotel(state: StatePart4) -> Literal["book_hotel_sensitive_tools", "book_hotel_safe_tools", "leave_skill", END]:
    route = tools_condition(state)
    if route == END: return END
    tool_calls = state["messages"][-1].tool_calls
    if not tool_calls: return END
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel: return "leave_skill"
    safe_toolnames = [t.name for t in book_hotel_safe_tools]
    if all(tc["name"] in safe_toolnames for tc in tool_calls): return "book_hotel_safe_tools"
    return "book_hotel_sensitive_tools"

builder_part4.add_edge("book_hotel_sensitive_tools", "book_hotel")
builder_part4.add_edge("book_hotel_safe_tools", "book_hotel")
builder_part4.add_conditional_edges(
    "book_hotel", route_book_hotel,
    ["leave_skill", "book_hotel_safe_tools", "book_hotel_sensitive_tools", END]
)

# --- Excursion Workflow Nodes ---
builder_part4.add_node(
    "enter_book_excursion", create_entry_node("Trip Recommendation Assistant", "book_excursion")
)
builder_part4.add_node("book_excursion", Assistant(book_excursion_runnable))
builder_part4.add_edge("enter_book_excursion", "book_excursion")
builder_part4.add_node("book_excursion_safe_tools", create_tool_node_with_fallback(book_excursion_safe_tools))
builder_part4.add_node("book_excursion_sensitive_tools", create_tool_node_with_fallback(book_excursion_sensitive_tools))

def route_book_excursion(state: StatePart4) -> Literal["book_excursion_sensitive_tools", "book_excursion_safe_tools", "leave_skill", END]:
    route = tools_condition(state)
    if route == END: return END
    tool_calls = state["messages"][-1].tool_calls
    if not tool_calls: return END
    did_cancel = any(tc["name"] == CompleteOrEscalate.__name__ for tc in tool_calls)
    if did_cancel: return "leave_skill"
    safe_toolnames = [t.name for t in book_excursion_safe_tools]
    if all(tc["name"] in safe_toolnames for tc in tool_calls): return "book_excursion_safe_tools"
    return "book_excursion_sensitive_tools"

builder_part4.add_edge("book_excursion_sensitive_tools", "book_excursion")
builder_part4.add_edge("book_excursion_safe_tools", "book_excursion")
builder_part4.add_conditional_edges(
    "book_excursion", route_book_excursion,
    ["book_excursion_safe_tools", "book_excursion_sensitive_tools", "leave_skill", END]
)

# --- Primary Assistant Nodes ---
builder_part4.add_node("primary_assistant", Assistant(assistant_runnable))
builder_part4.add_node("primary_assistant_tools", create_tool_node_with_fallback(primary_assistant_tools))

def route_primary_assistant(state: StatePart4) -> Literal[
    "enter_update_flight", "enter_book_car_rental", "enter_book_hotel",
    "enter_book_excursion", "primary_assistant_tools", END
]:
    route = tools_condition(state)
    if route == END: return END
    tool_calls = state["messages"][-1].tool_calls
    if not tool_calls: return END # Should not happen, but safeguard
    # Assuming single tool call for routing
    first_tool_name = tool_calls[0]["name"]
    if first_tool_name == ToFlightBookingAssistant.__name__: return "enter_update_flight"
    if first_tool_name == ToBookCarRental.__name__: return "enter_book_car_rental"
    if first_tool_name == ToHotelBookingAssistant.__name__: return "enter_book_hotel"
    if first_tool_name == ToBookExcursion.__name__: return "enter_book_excursion"
    # If it's a regular tool for the primary assistant
    if first_tool_name in [t.name for t in primary_assistant_tools]:
        return "primary_assistant_tools"
    # Fallback or error case
    print(f"Warning: Unhandled tool call '{first_tool_name}' in primary assistant routing.")
    return END # Or raise an error

builder_part4.add_conditional_edges(
    "primary_assistant", route_primary_assistant,
    [
        "enter_update_flight", "enter_book_car_rental", "enter_book_hotel",
        "enter_book_excursion", "primary_assistant_tools", END
    ]
)
builder_part4.add_edge("primary_assistant_tools", "primary_assistant")

# --- Entry Point Routing ---
def route_to_workflow(state: StatePart4) -> Literal[
    "primary_assistant", "update_flight", "book_car_rental", "book_hotel", "book_excursion"
]:
    """Routes to the correct workflow based on the current dialog_state."""
    dialog_state = state.get("dialog_state")
    # The state should have been initialized by fetch_user_info if it was empty
    if not dialog_state:
        print("Warning: dialog_state is empty in route_to_workflow. Defaulting to primary_assistant.")
        return "primary_assistant"
    return dialog_state[-1] # Route to the workflow at the top of the stack

builder_part4.add_conditional_edges("fetch_user_info", route_to_workflow)

# --- Compile Graph Part 4 ---
memory_part4 = MemorySaver()
part_4_graph = builder_part4.compile(
    checkpointer=memory_part4,
    interrupt_before=[
        "update_flight_sensitive_tools",
        "book_car_rental_sensitive_tools",
        "book_hotel_sensitive_tools",
        "book_excursion_sensitive_tools",
    ],
)

# --- Part 4 Graph Visualization (Commented out) ---
# try:
#     # display(Image(part_4_graph.get_graph(xray=True).draw_mermaid_png()))
#     part_4_graph.get_graph(xray=True).draw_mermaid_png(output_file_path="part4_graph.png")
#     print("Part 4 graph visualization saved to part4_graph.png")
# except Exception as e:
#     print(f"Could not generate Part 4 graph visualization: {e}")
#     pass

# --- Part 4 Example Conversation ---
if __name__ == "__main__":
    print("\n--- Running Part 4 Example Conversation ---")

    # Reset DB for this part
    db = update_dates(local_file)
    thread_id_part4 = str(uuid.uuid4())

    config_part4 = {
        "configurable": {
            "passenger_id": "3442 587242",
            "thread_id": thread_id_part4,
        }
    }

    _printed_part4 = set()
    current_step_event_part4 = None
    # Reuse tutorial questions
    for i, question in enumerate(tutorial_questions):
        print(f"\n--- Part 4 - Turn {i+1} ---")
        print(f"User: {question}")
        events = part_4_graph.stream(
            {"messages": [("user", question)]}, config_part4, stream_mode="values"
        )
        for event in events:
            _print_event(event, _printed_part4)
            if "messages" in event:
                 current_step_event_part4 = event

        snapshot = part_4_graph.get_state(config_part4)
        while snapshot and snapshot.next: # Check if snapshot is not None
            # Handle interrupt logic (same as Part 2 & 3)
            print("\n--- INTERRUPT (Sensitive Tool) ---")
            try:
                user_input = input(
                    "Do you approve of the above actions? Type 'y' to continue; "
                    "otherwise, explain your requested changes.\n\n"
                )
            except EOFError:
                print("Non-interactive mode detected, defaulting to 'y'.")
                user_input = "y"

            if user_input.strip().lower() == "y":
                print("Continuing...")
                events = part_4_graph.stream(None, config_part4, stream_mode="values")
            else:
                print("User disapproved or requested changes. Providing feedback...")
                if current_step_event_part4 and current_step_event_part4["messages"][-1].tool_calls:
                     tool_call_id = current_step_event_part4["messages"][-1].tool_calls[0]["id"]
                     tool_message = ToolMessage(
                         tool_call_id=tool_call_id,
                         content=f"API call denied by user. Reasoning: '{user_input}'. Continue assisting, accounting for the user's input.",
                     )
                     events = part_4_graph.stream({"messages": [tool_message]}, config_part4, stream_mode="values")
                else:
                     print("Error: Could not get tool_call_id for feedback. Continuing without feedback.")
                     events = part_4_graph.stream(None, config_part4, stream_mode="values")


            for event in events:
                _print_event(event, _printed_part4)
                if "messages" in event:
                     current_step_event_part4 = event

            # Update snapshot after resuming
            snapshot = part_4_graph.get_state(config_part4)


    print("\n=== Tutorial Conclusion ===")
    print("Part 4 demonstrated specialized workflows for better task handling.")
    print("Further improvements would involve adding evaluations (e.g., using LangSmith).")