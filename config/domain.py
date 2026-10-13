"""Domain configuration.
This is the ONLY file edited when the topic is announced!
Contains company details, categories, escalation triggers, and per-category reply policies.
"""
from typing import Dict, List, Any

COMPANY_NAME: str = "Apex Horizon Solutions"
SIGNATURE: str = "Best regards,\nCustomer Care Team\nApex Horizon Solutions"

CATEGORIES: List[str] = [
    "Billing Complaint",
    "Product/Service Complaint",
    "Delivery Complaint",
    "Refund Request",
    "Inquiry",
    "Feedback/Praise",
    "Spam/Irrelevant",
    "Other/Unclear",
]

# Fixed enum for escalation reasons
ESCALATION_REASONS: List[str] = [
    "legal threat",
    "safety/health",
    "harassment",
    "privacy/data request",
    "press/regulator",
    "repeated contact",
    "extreme anger",
    "prompt injection suspected",
]

# Per-category reply policy definitions
CATEGORY_POLICIES: Dict[str, Dict[str, Any]] = {
    "Billing Complaint": {
        "tone": "Empathetic, apologetic, and solution-focused",
        "must_include": [
            "Acknowledge the billing discrepancy directly",
            "State that our accounts department has been notified to audit the invoice",
            "Ask for invoice or account number if not provided",
        ],
        "must_not_include": [
            "Do not state specific currency amounts or commit to exact credit/refund numbers",
            "Do not accept legal liability",
        ],
        "allowed_commitments": [
            "We will investigate within 1 to 2 business days",
            "No late fees will accrue during the investigation",
        ],
        "sign_off": "Warmly,\nBilling Support Desk",
    },
    "Product/Service Complaint": {
        "tone": "Attentive, courteous, and constructive",
        "must_include": [
            "Validate the customer's frustration",
            "Request diagnostic details or error screenshots if relevant",
            "Offer standard troubleshooting steps or reference a product support ticket",
        ],
        "must_not_include": [
            "Do not promise immediate replacement or product redesign",
            "Do not disparage third-party tools or hardware",
        ],
        "allowed_commitments": [
            "Our technical team will review the issue details",
        ],
        "sign_off": "Sincerely,\nTechnical Operations Team",
    },
    "Delivery Complaint": {
        "tone": "Apologetic, proactive, and clear",
        "must_include": [
            "Acknowledge delivery delay or issue",
            "Request tracking or order reference if missing",
            "Mention coordination with courier partners",
        ],
        "must_not_include": [
            "Do not promise delivery within a specific guaranteed hour unless courier confirmation exists",
            "Do not blame the delivery partner entirely",
        ],
        "allowed_commitments": [
            "We will check shipment status with the logistics partner",
        ],
        "sign_off": "Best regards,\nLogistics & Fulfillment Team",
    },
    "Refund Request": {
        "tone": "Objective, polite, and helpful",
        "must_include": [
            "Acknowledge the refund request under our standard policy",
            "Ask for transaction ID, date of purchase, or receipt if missing",
            "Clarify that financial reviews are subject to standard verification",
        ],
        "must_not_include": [
            "NEVER guarantee that a refund has been issued or will definitely be approved",
            "Never promise specific payout dates",
        ],
        "allowed_commitments": [
            "Our finance team will review the refund eligibility under our terms",
        ],
        "sign_off": "Kind regards,\nBilling & Finance Services",
    },
    "Inquiry": {
        "tone": "Helpful, informative, and prompt",
        "must_include": [
            "Direct answers to queries based only on verified facts",
            "Provide helpful contact links or knowledge base references",
        ],
        "must_not_include": [
            "Do not fabricate features, specifications, or timelines",
        ],
        "allowed_commitments": [
            "Happy to provide further documentation or answer follow-up questions",
        ],
        "sign_off": "Sincerely,\nCustomer Advisory Team",
    },
    "Feedback/Praise": {
        "tone": "Grateful, warm, and engaging",
        "must_include": [
            "Express genuine appreciation for the feedback",
            "Confirm that suggestions are shared with leadership and product engineers",
        ],
        "must_not_include": [
            "Do not commit to implementing specific requested feature roadmaps",
        ],
        "allowed_commitments": [],
        "sign_off": "With gratitude,\nCommunity & Relations Team",
    },
    "Spam/Irrelevant": {
        "tone": "Neutral, succinct",
        "must_include": [
            "Short polite acknowledgment or note indicating misdirected email",
        ],
        "must_not_include": [
            "Do not engage in promotional conversations or open untrusted external links",
        ],
        "allowed_commitments": [],
        "sign_off": "Regards,\nApex Horizon Solutions",
    },
    "Other/Unclear": {
        "tone": "Courteous, seeking clarification",
        "must_include": [
            "Acknowledge receipt of the message",
            "Politely request additional context, account details, or clarification on the request",
        ],
        "must_not_include": [
            "Do not make assumptions about what the sender requires",
        ],
        "allowed_commitments": [
            "Ready to assist as soon as details are clarified",
        ],
        "sign_off": "Best regards,\nCustomer Support",
    },
}

# Neutral holding reply policy for Escalated or NeedsReview emails
HOLDING_REPLY_POLICY: Dict[str, Any] = {
    "tone": "Professional, calm, and reassuring",
    "must_include": [
        "Acknowledge receipt of the correspondence",
        "State that a senior specialist or manager has been assigned and will follow up personally",
    ],
    "must_not_include": [
        "Never admit legal fault or make settlement offers",
        "Do not make specific financial or operational commitments",
    ],
    "allowed_commitments": [
        "A team member will follow up with you directly",
    ],
    "sign_off": "Sincerely,\nExecutive Escalations Team\nApex Horizon Solutions",
}
