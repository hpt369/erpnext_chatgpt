# -*- coding: utf-8 -*-
import json
from typing import List, Dict, Any

import frappe
from frappe import _

# ▶─────────  OPENAI v0.x  ─────────◀
import openai              # ✅ old SDK
#  ──────────────────────────────────

from erpnext_chatgpt.erpnext_chatgpt.tools import get_tools, available_functions

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------
PRE_PROMPT = (
    "You are an AI assistant integrated with ERPNext. Please provide accurate "
    f"and helpful responses based on the following questions and data provided "
    f"by the user. The current date is {frappe.utils.now()}."
)
MODEL       = "gpt-4o-mini"   # or whichever model your key has access to
MAX_TOKENS  = 8_000

# ---------------------------------------------------------------------------
# OPENAI INITIALISATION
# ---------------------------------------------------------------------------
def prime_openai() -> None:
    """
    Initialise the global openai object with the API key from the doctype.
    v0.x uses module-level attributes instead of a client instance.
    """
    api_key = frappe.db.get_single_value("OpenAI Settings", "api_key")
    if not api_key:
        frappe.throw(_("OpenAI API key is not set in OpenAI Settings."))
    openai.api_key = api_key


# ---------------------------------------------------------------------------
# FUNCTION-CALL HANDLING (v0.x style)
# ---------------------------------------------------------------------------
def run_function_call(
    function_call: Dict[str, Any],
    conversation: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Execute the function returned by the model and append its result so the
    assistant can see it on the next turn.

    v0.x responses have **one** `function_call` dict with keys `name` and
    `arguments` (JSON string).
    """
    function_name = function_call.get("name")
    function_to_call = available_functions.get(function_name)

    if not function_to_call:
        frappe.log_error(
            f"Function {function_name} not found.", "OpenAI Function Error"
        )
        raise ValueError(f"Function {function_name} not found.")

    try:
        parsed_args = json.loads(function_call.get("arguments", "{}"))
        result      = function_to_call(**parsed_args)
    except Exception as ex:
        frappe.log_error(
            f"Error calling function {function_name} with args "
            f"{json.dumps(parsed_args)}: {str(ex)}",
            "OpenAI Function Error",
        )
        raise

    # ▸ append a “function” role message per OpenAI docs
    conversation.append(
        {
            "role":    "function",
            "name":    function_name,
            "content": str(result),
        }
    )
    return conversation


# ---------------------------------------------------------------------------
# UTILITY: crude token estimator
# ---------------------------------------------------------------------------
def estimate_token_count(messages: List[Dict[str, Any]]) -> int:
    tokens_per_message = 4
    tokens_per_word    = 1.5
    return sum(
        tokens_per_message + int(len(str(m.get("content", "")).split()) * tokens_per_word)
        for m in messages
        if m.get("content") is not None
    )


def trim_conversation_to_token_limit(
    conversation: List[Dict[str, Any]], token_limit: int = MAX_TOKENS
) -> List[Dict[str, Any]]:
    while estimate_token_count(conversation) > token_limit and len(conversation) > 1:
        # drop oldest non-system msg
        for i, msg in enumerate(conversation):
            if msg.get("role") != "system":
                del conversation[i]
                break
    return conversation


# ---------------------------------------------------------------------------
# PUBLIC FRAPPE ENDPOINTS
# ---------------------------------------------------------------------------
@frappe.whitelist()
def ask_openai_question(conversation: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    The main gateway called by the client code / UI.
    """
    try:
        prime_openai()

        # prepend system prompt if missing
        if not conversation or conversation[0].get("role") != "system":
            conversation.insert(0, {"role": "system", "content": PRE_PROMPT})

        conversation = trim_conversation_to_token_limit(conversation)

        frappe.logger("OpenAI").debug(f"Conversation ➜ {json.dumps(conversation)}")

        # --- FIRST PASS -----------------------------------------------------
        raw_tools = get_tools()

        functions = [
            t["function"] if t.get("type") == "function" and "function" in t else t
            for t in raw_tools
        ]
        first_resp = openai.ChatCompletion.create(
            model           = MODEL,
            messages        = conversation,
            functions       = functions,
            function_call   = "auto",   # let the model decide
        )

        assistant_msg = first_resp.choices[0].message
        frappe.logger("OpenAI").debug(f"OpenAI Response ➜ {assistant_msg}")

        # --- FUNCTION CALL? -------------------------------------------------
        if assistant_msg.get("function_call"):
            conversation.append(assistant_msg)           # store the assistant request
            conversation = run_function_call(
                assistant_msg["function_call"], conversation
            )
            conversation = trim_conversation_to_token_limit(conversation)

            # --- SECOND PASS ------------------------------------------------
            second_resp = openai.ChatCompletion.create(
                model    = MODEL,
                messages = conversation,
            )
            return second_resp.choices[0].message

        # --- NO FUNCTION CALL ----------------------------------------------
        return assistant_msg

    except Exception as ex:
        frappe.log_error(str(ex), "OpenAI API Error")
        return {"error": str(ex)}


@frappe.whitelist()
def test_openai_api_key(api_key: str) -> bool:
    """
    Quick connectivity test for a key using v0.x syntax.
    """
    try:
        openai.api_key = api_key
        openai.Model.list()      # lightweight call
        return True
    except Exception as ex:
        frappe.log_error(str(ex), "OpenAI API Key Test Failed")
        return False


@frappe.whitelist()
def check_openai_key_and_role() -> Dict[str, Any]:
    """
    Validate that the current user is a System Manager and the key works.
    """
    if "System Manager" not in frappe.get_roles(frappe.session.user):
        return {"show_button": False, "reason": "Only System Managers can access."}

    api_key = frappe.db.get_single_value("OpenAI Settings", "api_key")
    if not api_key:
        return {"show_button": False, "reason": "OpenAI API key is not set in OpenAI Settings."}

    try:
        openai.api_key = api_key
        openai.Model.list()
        return {"show_button": True}
    except Exception as ex:
        return {"show_button": False, "reason": str(ex)}
