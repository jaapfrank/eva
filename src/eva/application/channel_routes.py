"""Structural channel IDs; authority remains with the current owner binding."""
import re


def is_whatsapp_conversation(value):
    return isinstance(value, str) and re.fullmatch(r"whatsapp:[1-9][0-9]{6,14}", value) is not None
