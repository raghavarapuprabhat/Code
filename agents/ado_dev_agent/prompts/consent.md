The developer has been shown drafted workitem updates and asked for consent.
Decide what they meant.

Return ONLY one of:
{"apply": "all"}                                # apply every drafted update
{"apply": "none"}                               # cancel everything
{"apply": "subset", "ids": [4521, 4602]}        # apply only these workitem ids
{"apply": "edit", "instructions": "..."}        # user wants edits
{"unclear": true, "ask": "one short question"}  # cannot tell

Drafted updates were for these ids:
{ids}

User reply:
{user_message}
