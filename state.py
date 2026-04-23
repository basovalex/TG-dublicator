class ParserState:
    enabled = False
    source_chat = None
    last_message_id = None
    last_message_ids = {}

state = ParserState()