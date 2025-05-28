import frappe
from frappe.utils import now
from frappe import _

from erpnext_chatgpt.erpnext_chatgpt.api import ask_openai_question   # <- the file you rewrote

SYSTEM_PROMPT = (
    "You are an AI support agent for {company}. Draft a clear, polite, accurate "
    "reply to the customer's e-mail. If you need ERP data use the available "
    "functions instead of guessing."
)

def reply_to_incoming(doc, method=None):
    """Called whenever a Communication is inserted."""
    # 1 · guard clauses – fire only on inbound messages
    if doc.communication_type != "Communication":
        return
    if doc.sent_or_received != "Received":
        return

    # 2 · build the conversation
    conversation = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT.format(company=frappe.defaults.get_global_default("company_name") or "our company")
        },
        {
            "role": "user",
            "name": doc.sender or "customer",
            "content": doc.content or _(u"(no body)")
        },
    ]

    # 3 · call ChatGPT (this will run the same 2-pass logic, tools etc.)
    result = ask_openai_question(conversation)

    if result.get("error"):
        frappe.log_error(result["error"], "Auto-reply failed")
        return

    reply_content = result["content"]

    # 4 · create a **draft** Communication so staff can review & send
    draft = frappe.new_doc("Communication")
    draft.communication_type  = "Communication"     # a regular mail
    draft.sent_or_received    = "Sent"
    draft.subject             = f"Re: {doc.subject or ''}".strip()
    draft.content             = reply_content
    draft.reference_doctype   = doc.reference_doctype
    draft.reference_name      = doc.reference_name
    draft.recipient           = doc.sender
    draft.status              = "Open"              # shows up in timeline but not sent
    draft.unread_notification_sent = 0              # keep it unread for assignee
    draft.flags.draft_reply   = True                # custom flag so you can filter
    draft.insert(ignore_permissions=True)

    # 5 · (optional) notify an agent / Slack channel that a draft is ready
    frappe.publish_realtime("auto_email_drafted", {"docname": draft.name})
