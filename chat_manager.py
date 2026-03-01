"""
Chat history management for the AI Chat App
"""
from __future__ import annotations
import json
import os
import re
import uuid
from datetime import datetime
from typing import List, Dict, Optional, Any

from api_clients import estimate_tokens


class ChatManager:
    def __init__(self, chats_directory: str = "chats"):
        self.chats_directory = chats_directory
        self.current_chat: Dict[str, Any] = {
            "chat_history": [],
            "conversation_summary": "",
            "token_count": 0,
        }
        self.current_chat_file: Optional[str] = None
        self.branch_db = None  # Set by FlaskChatApp after init
        os.makedirs(self.chats_directory, exist_ok=True)

        # Migrate old chats to include chat_id fields
        self.migrate_old_chats()

    # ---------- file ops ----------
    def create_new_chat(self, title: Optional[str] = None) -> str:
        title = title or "New Chat"
        clean = re.sub(r"[^\w\s-]", "", title)
        clean = re.sub(r"[-\s]+", "_", clean)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"{clean}_{ts}.json"
        
        # Generate a unique chat ID
        chat_id = str(uuid.uuid4())

        self.current_chat = {
            "chat_id": chat_id,
            "root_chat_id": chat_id,
            "parent_chat_id": "",
            "chat_history": [],
            "conversation_summary": "",
            "token_count": 0,
            "title": title
        }
        self.current_chat_file = fn
        self.save_current_chat(force_save=True)
        return fn

    def load_chat(self, filename: str) -> bool:
        path = os.path.join(self.chats_directory, filename)
        try:
            if not os.path.exists(path):
                return False
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                hist = data
                tk = sum(estimate_tokens(m.get("content", "")) for m in hist)
                # Generate chat_id for backward compatibility if not present
                chat_id = str(uuid.uuid4())
                self.current_chat = {
                    "chat_id": chat_id,
                    "root_chat_id": chat_id,
                    "parent_chat_id": "",
                    "chat_history": hist,
                    "conversation_summary": "",
                    "token_count": tk,
                }
            else:
                # Ensure chat_id exists for backward compatibility
                if "chat_id" not in data:
                    data["chat_id"] = str(uuid.uuid4())
                if "root_chat_id" not in data:
                    data["root_chat_id"] = data["chat_id"]
                if "parent_chat_id" not in data:
                    data["parent_chat_id"] = ""
                self.current_chat = data
            self.current_chat_file = filename
            return True
        except Exception as e:
            print(f"Error loading chat {filename}: {e}")
            return False

    def save_current_chat(self, force_save: bool = False) -> bool:
        if not self.current_chat_file:
            return False
        if not force_save and not self.current_chat.get("chat_history"):
            return False
            
        # If self.current_chat.get("title") is empty:
        if not self.current_chat.get("title"):
            # Loop through self.current_chat["chat_history"]
            for message in self.current_chat.get("chat_history", []):
                # Find the first message with role == "user"
                if message.get("role") == "user":
                    # Use the first ~60 characters of its content as self.current_chat["title"]
                    content = message.get("content", "")
                    self.current_chat["title"] = content[:60] + ("..." if len(content) > 60 else "")
                    break
        
        path = os.path.join(self.chats_directory, self.current_chat_file)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.current_chat, f, indent=2, ensure_ascii=False)

            # Sync metadata to branch database if available
            self._sync_to_branch_db()

            return True
        except Exception as e:
            print(f"Error saving chat: {e}")
            return False

    def _sync_to_branch_db(self) -> None:
        """Sync current chat metadata to the branch database."""
        if not self.branch_db:
            return
        chat_id = self.current_chat.get("chat_id")
        if not chat_id:
            return
        try:
            hist = self.current_chat.get("chat_history", [])
            self.branch_db.upsert_branch(
                chat_id,
                title=self.current_chat.get("title", ""),
                parent_id=self.current_chat.get("parent_chat_id") or None,
                root_id=self.current_chat.get("root_chat_id") or chat_id,
                token_count=self.current_chat.get("token_count", 0),
                message_count=len(hist),
                filename=self.current_chat_file,
            )
        except Exception as e:
            print(f"[BranchDB] Sync error: {e}")

    def delete_chat(self, filename: str) -> bool:
        path = os.path.join(self.chats_directory, filename)
        try:
            if os.path.exists(path):
                os.remove(path)
                if self.current_chat_file == filename:
                    self.current_chat_file = None
                    self.current_chat = {
                        "chat_history": [],
                        "conversation_summary": "",
                        "token_count": 0,
                    }
                return True
        except Exception as e:
            print(f"Error deleting chat {filename}: {e}")
        return False

    def get_chat_list(self) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        try:
            for fn in os.listdir(self.chats_directory):
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(self.chats_directory, fn)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        hist = data
                        tk = sum(estimate_tokens(m.get("content", "")) for m in hist)
                    else:
                        hist = data.get("chat_history", [])
                        tk = data.get("token_count", 0)
                        # Use title from JSON if available, otherwise fall back to filename/first message
                        title = data.get("title", fn[:-5].replace("_", " "))
                    if hist and not title:
                        first_user = next(
                            (m for m in hist if m.get("role") == "user"), None
                        )
                        if first_user:
                            c = first_user.get("content", "")
                            title = c[:50] + ("..." if len(c) > 50 else "")
                    elif not title:
                        title = fn[:-5].replace("_", " ")
                    mod = datetime.fromtimestamp(os.path.getmtime(path)).strftime(
                        "%Y-%m-%d %H:%M"
                    )
                    depth = 0
                    if fn.startswith("side_"):
                        depth = fn.count("side_")
                    parent_chat_id = (
                        data.get("parent_chat_id", "") if isinstance(data, dict) else ""
                    )
                    root_chat_id = (
                        data.get("root_chat_id", parent_chat_id)
                        if isinstance(data, dict)
                        else ""
                    )
                    # Get chat_id from the data, or generate one for backward compatibility
                    chat_id = ""
                    if isinstance(data, dict):
                        chat_id = data.get("chat_id", "")
                    
                    out.append(
                        {
                            "filename": fn,
                            "title": title,
                            "modified": mod,
                            "messages": len(hist),
                            "tokens": tk,
                            "depth": depth,
                            "parent_chat_id": parent_chat_id,
                            "root_chat_id": root_chat_id,
                            "chat_id": chat_id
                        }
                    )
                except Exception as e:
                    print(f"Error reading chat {fn}: {e}")
            out.sort(key=lambda x: x["modified"], reverse=True)
        except Exception as e:
            print(f"Error listing chats: {e}")
        return out

    def migrate_old_chats(self) -> None:
        """Migrate old chat files to include chat_id fields for backward compatibility."""
        try:
            for fn in os.listdir(self.chats_directory):
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(self.chats_directory, fn)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # Check if this is an old format that needs migration
                    if isinstance(data, dict):
                        # If it already has chat_id, no migration needed
                        if "chat_id" in data:
                            continue
                    
                    # Add chat_id fields for backward compatibility
                    if isinstance(data, list):
                        # Convert list format to dict format with chat_id
                        data = {
                            "chat_id": str(uuid.uuid4()),
                            "root_chat_id": str(uuid.uuid4()),
                            "parent_chat_id": "",
                            "chat_history": data,
                            "conversation_summary": "",
                            "token_count": sum(estimate_tokens(m.get("content", "")) for m in data)
                        }
                    elif isinstance(data, dict):
                        # Add missing chat_id fields
                        if "chat_id" not in data:
                            data["chat_id"] = str(uuid.uuid4())
                        if "root_chat_id" not in data:
                            data["root_chat_id"] = data["chat_id"]
                        if "parent_chat_id" not in data:
                            data["parent_chat_id"] = ""
                        if "conversation_summary" not in data:
                            data["conversation_summary"] = ""
                        if "token_count" not in data:
                            data["token_count"] = sum(estimate_tokens(m.get("content", "")) for m in data.get("chat_history", []))
                    
                    # Save the updated data
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                        
                except Exception as e:
                    print(f"Error migrating chat {fn}: {e}")
        except Exception as e:
            print(f"Error migrating chats: {e}")

    # ---------- messages ----------
    def _auto_rename_chat_from_first_message(self, first_message: str) -> None:
        if not self.current_chat_file:
            return
        words = first_message.strip().split()
        title = " ".join(words[:4]) or "Chat"
        clean = re.sub(r"[^\w\s-]", "", title)
        clean = re.sub(r"[-\s]+", "_", clean)
        base = self.current_chat_file[:-5]
        m = re.search(r"_(\d{8}_\d{6})$", base)
        ts = m.group(1) if m else datetime.now().strftime("%Y%m%d_%H%M%S")
        new_fn = f"{clean}_{ts}.json"
        old = os.path.join(self.chats_directory, self.current_chat_file)
        new = os.path.join(self.chats_directory, new_fn)
        try:
            if os.path.exists(old):
                # Update the title in the current chat data
                self.current_chat["title"] = title
                # Save the chat with the new title before renaming
                self.save_current_chat(force_save=True)
                os.rename(old, new)
                self.current_chat_file = new_fn
                print(f"Chat renamed from {os.path.basename(old)} to {new_fn}")
        except Exception as e:
            print(f"Error renaming chat file: {e}")

    def add_message(
        self,
        role: str,
        content: str,
        model_name: Optional[str] = None,
        timestamp: Optional[str] = None,
        auto_save: bool = True,
    ) -> None:
        ts = timestamp or datetime.now().strftime("%H:%M")
        msg: Dict[str, Any] = {"role": role, "content": content, "timestamp": ts}
        if model_name:
            msg["model"] = model_name
        self.current_chat.setdefault("chat_history", []).append(msg)
        # REMOVE this block (prevents unwanted filename/title changes)
        # if (
        #     role == "user"
        #     and len(self.current_chat["chat_history"]) == 1
        #     and not (self.current_chat_file or "").startswith("side_")
        # ):
        #     self._auto_rename_chat_from_first_message(content)
        self.current_chat["token_count"] = sum(
            estimate_tokens(m["content"]) for m in self.current_chat["chat_history"]
        )
        if auto_save:
            self.save_current_chat()

    def get_messages(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        msgs = self.current_chat.get("chat_history", [])
        if limit:
            return msgs[-limit:]  # Slice creates a new list
        else:
            return list(msgs)  # Explicit copy

    def get_token_count(self) -> int:
        """Return the estimated token count for the current chat's history."""
        return self.current_chat.get("token_count", 0)

    def get_conversation_context(self, max_messages: int = 10) -> List[Dict[str, str]]:
        msgs = self.get_messages(max_messages)
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    def get_selected_context(self, selected_indices: List[int], selected_summaries: List[int] = None) -> List[Dict[str, str]]:
        """
        Build context from selected messages and summaries.

        Logic:
        - If message index is in selected_indices AND selected_summaries, use the summary (if exists)
        - If message index is in selected_indices but NOT in selected_summaries, use full content
        - If message index is in selected_summaries but NOT in selected_indices, use just the summary
        """
        msgs = self.get_messages()
        out: List[Dict[str, str]] = []
        selected_summaries = selected_summaries or []
        summary_set = set(selected_summaries)
        msg_set = set(selected_indices)

        # Track which indices we've processed
        processed = set()

        # First, process selected messages
        for i in selected_indices:
            if 0 <= i < len(msgs):
                m = msgs[i]
                # If summary is also selected and exists, use summary
                if i in summary_set and m.get("summary"):
                    out.append({"role": m["role"], "content": m["summary"]})
                else:
                    # Use full content
                    out.append({"role": m["role"], "content": m["content"]})
                processed.add(i)

        # Then, add summaries that are selected but their messages are NOT
        # These go at the end, preserving the order
        for i in selected_summaries:
            if i not in processed and 0 <= i < len(msgs):
                m = msgs[i]
                if m.get("summary"):
                    out.append({"role": m["role"], "content": m["summary"]})

        return out

    def add_summary(self, message_index: int, summary_content: str, summary_model: Optional[str] = None) -> bool:
        msgs = self.current_chat.get("chat_history", [])
        if 0 <= message_index < len(msgs):
            msgs[message_index]["summary"] = summary_content
            if summary_model is not None:
                msgs[message_index]["summary_model"] = summary_model
            self.current_chat["token_count"] = sum(
                estimate_tokens(m.get("content", "")) for m in msgs
            )
            self.save_current_chat()
            return True
        return False

    def update_title(self, new_title: str) -> bool:
        """Update chat title only if it's still the default 'New Chat' or empty (for branched chats)."""
        current_title = self.current_chat.get("title", "")
        if current_title != "New Chat" and current_title != "":
            return False  # Already has a custom title
        self.current_chat["title"] = new_title
        self.save_current_chat(force_save=True)
        return True

    def clear_current_chat(self, auto_save: bool = True) -> None:
        self.current_chat = {
            "chat_history": [],
            "conversation_summary": "",
            "token_count": 0,
        }
        if self.current_chat_file and auto_save:
            self.save_current_chat()
