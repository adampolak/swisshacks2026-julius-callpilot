from gi_loader import load_gi_bundle


GI_BUNDLE = load_gi_bundle()
DEMO_CLM_PROFILE = GI_BUNDLE["profile"]
DEMO_TRANSCRIPT = GI_BUNDLE["transcript"]
GI_SYSTEM_PROMPT = GI_BUNDLE["system_prompt"]
GI_ECONOMIC_CONTEXT = GI_BUNDLE["economic_context"]
DEMO_STEERING_RULES = []


MOCK_ROUND_1 = {
    "silent": False,
    "cards": [
        {
            "category": "RECALL",
            "text": "Private-credit research deck is still outstanding. Acknowledge it and commit to a delivery date.",
        },
        {
            "category": "OPPORTUNITY",
            "text": "Combine succession and foundation planning only after confirming Andreas wants both workstreams coordinated.",
        },
    ],
    "model_source": "mock",
}


MOCK_ROUND_2 = {
    "executive_summary": "Andreas Vogel reviewed family priorities, portfolio concentration and several unresolved planning matters. He asked for live gold performance, proposed a CHF 3-4M direct Nvidia position, and reaffirmed that he will not sell his retained Sensoria stake. The conversation also surfaced succession concerns involving his father and sister, Claudia's art-education foundation, and the overdue private-credit research deck. The RM handled the emotional topics carefully but did not close the gold question or the outstanding research commitment during the call.",
    "client_objectives": [
        "Assess a large direct Nvidia purchase without losing sight of existing technology concentration.",
        "Protect the retained Sensoria value without requiring a sale.",
        "Structure support for Sophie and Luca rather than making unplanned transfers.",
        "Coordinate cross-border succession planning and Claudia's philanthropic plans.",
    ],
    "relationship_signals": [
        "Andreas values decisiveness, plain language and evidence that personal details are remembered.",
        "He remains emotionally anchored to Sensoria and frames concentration as the source of his success.",
        "Family health and estate tension require empathy before technical planning.",
    ],
    "unresolved_questions": [
        "Current verified performance of the CHF 1.35M gold holding.",
        "Acceptable size and risk budget for any additional single-name technology exposure.",
        "Scope and timing for the cross-border succession-planning meeting.",
        "Delivery date for the outstanding private-credit research deck.",
        "Funding structure, amount and safeguards for Sophie and Luca.",
    ],
    "improvement_opportunities": [
        "Return explicitly to interrupted questions, beginning with verified gold performance.",
        "Frame Nvidia sizing around protecting what Andreas built, not a generic diversification lecture.",
        "Close every prior commitment with an owner and delivery date.",
        "Separate empathy, discovery and technical follow-up on succession matters.",
    ],
    "suitability_and_compliance": [
        "Do not invent live gold performance; retrieve and verify current market data.",
        "Document how a CHF 3-4M Nvidia position compounds the existing CHF 3M technology sleeve.",
        "Never suggest directly selling the emotionally held Sensoria stake.",
        "Use qualified advisers for cross-border estate, legal and tax conclusions.",
    ],
    "recommended_follow_ups": [
        "Send verified gold performance and portfolio contribution figures.",
        "Prepare Nvidia sizing scenarios against the balanced mandate and existing concentration.",
        "Arrange the agreed cross-border wealth-planning introduction.",
        "Deliver the private-credit research deck with a clear date and accountable owner.",
        "Schedule separate discovery sessions for the foundation and next-generation funding needs.",
    ],
    "draft_client_note": "Dear Andreas, thank you for the candid discussion. I will send the verified gold figures, prepare clear sizing scenarios for the proposed Nvidia position in the context of your existing technology exposure, and confirm the cross-border planning introduction. I will also close the outstanding private-credit research item with a firm delivery date. We can then decide how best to coordinate the succession, foundation and next-generation workstreams without turning the next meeting into a product discussion.",
}


MOCK_ROUND_3 = {
    **MOCK_ROUND_2,
    "executive_summary": "The immediate priority is disciplined follow-through: verify the unanswered gold data, assess the proposed Nvidia position against existing concentration, and deliver the overdue private-credit research. Succession and philanthropy should proceed as coordinated but distinct planning workstreams. Every next step should use plain language, named owners and delivery dates while respecting Andreas's attachment to Sensoria.",
    "improvement_opportunities": [
        "Open the next contact by closing the gold and private-credit commitments.",
        "Use one-page scenarios to compare Nvidia position sizes and total technology exposure.",
        "Record explicit owners and dates for each planning workstream.",
        "Keep Sensoria discussions focused on value protection without a sale.",
    ],
}
