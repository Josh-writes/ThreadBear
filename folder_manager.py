"""
Folder-based organization system for ThreadBear.

Manages hierarchical folders (max 2 levels: root + subfolder) for organizing
chats and documents. Persisted in folders.json.
"""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional


class FolderManager:
    def __init__(self, config_path: str = "folders.json",
                 chats_directory: str = "chats"):
        self.config_path = config_path
        self.chats_directory = chats_directory
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        """Load folders.json or create default structure."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Ensure required keys exist
                data.setdefault("version", "1.0")
                data.setdefault("folders", [])
                data.setdefault("chat_folder_map", {})
                data.setdefault("file_folder_map", {})
                return data
            except Exception as e:
                print(f"Error loading folders.json: {e}")
        return {
            "version": "1.0",
            "folders": [],
            "chat_folder_map": {},
            "file_folder_map": {},
        }

    def _save(self) -> None:
        """Persist folders.json."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving folders.json: {e}")

    def _find_folder(self, folder_id: str) -> Optional[Dict[str, Any]]:
        """Find a folder by ID."""
        for folder in self.data["folders"]:
            if folder["id"] == folder_id:
                return folder
        return None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ---- Folder CRUD ----

    def create_folder(self, name: str,
                      parent_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new folder. Returns the created folder dict."""
        # Validate parent exists and enforce max depth of 2
        if parent_id:
            parent = self._find_folder(parent_id)
            if not parent:
                raise ValueError(f"Parent folder {parent_id} not found")
            if parent.get("parent_id"):
                raise ValueError("Maximum folder depth is 2 (root + subfolder)")

        # Check name uniqueness within same parent
        siblings = [f for f in self.data["folders"]
                    if f.get("parent_id") == parent_id]
        if any(s["name"] == name for s in siblings):
            raise ValueError(f"Folder '{name}' already exists at this level")

        # Calculate order (append at end)
        order = max((s.get("order", 0) for s in siblings), default=-1) + 1

        folder = {
            "id": str(uuid.uuid4()),
            "name": name,
            "parent_id": parent_id,
            "order": order,
            "created": self._now_iso(),
            "prompt_branch_filename": None,
            "saved_prompts": [],
            "active_prompt_id": None,
            "memory_notes": [],
        }
        self.data["folders"].append(folder)

        # Auto-create the prompt branch chat
        prompt_fn = self._create_prompt_branch(folder["id"], name)
        folder["prompt_branch_filename"] = prompt_fn

        self._save()
        return folder

    def rename_folder(self, folder_id: str, new_name: str) -> bool:
        """Rename a folder."""
        folder = self._find_folder(folder_id)
        if not folder:
            return False

        # Check uniqueness among siblings
        siblings = [f for f in self.data["folders"]
                    if f.get("parent_id") == folder.get("parent_id")
                    and f["id"] != folder_id]
        if any(s["name"] == new_name for s in siblings):
            raise ValueError(f"Folder '{new_name}' already exists at this level")

        folder["name"] = new_name
        
        # Update the prompt branch chat title
        prompt_fn = folder.get("prompt_branch_filename")
        if prompt_fn:
            prompt_path = os.path.join(self.chats_directory, prompt_fn)
            if os.path.exists(prompt_path):
                try:
                    with open(prompt_path, "r", encoding="utf-8") as f:
                        prompt_data = json.load(f)
                    prompt_data["title"] = f"[Prompt] {new_name}"
                    with open(prompt_path, "w", encoding="utf-8") as f:
                        json.dump(prompt_data, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    print(f"Error updating prompt branch title: {e}")
        
        self._save()
        return True

    def move_folder(self, folder_id: str,
                    new_parent_id: Optional[str] = None) -> bool:
        """Move folder to new parent. None = root level."""
        folder = self._find_folder(folder_id)
        if not folder:
            return False

        if new_parent_id:
            parent = self._find_folder(new_parent_id)
            if not parent:
                raise ValueError(f"Parent folder {new_parent_id} not found")
            if parent.get("parent_id"):
                raise ValueError("Cannot nest deeper than 2 levels")
            if new_parent_id == folder_id:
                raise ValueError("Cannot move folder into itself")

        folder["parent_id"] = new_parent_id
        self._save()
        return True

    def delete_folder(self, folder_id: str,
                      delete_contents: bool = False,
                      move_to_parent: bool = True) -> Dict[str, Any]:
        """Delete a folder.

        Returns dict with info about what happened:
        { "deleted": True, "moved_chats": [...], "moved_files": [...],
          "deleted_subfolders": [...] }
        """
        folder = self._find_folder(folder_id)
        if not folder:
            return {"deleted": False, "error": "not_found"}

        result = {
            "deleted": True,
            "moved_chats": [],
            "moved_files": [],
            "deleted_subfolders": [],
        }

        parent_id = folder.get("parent_id")

        # Handle subfolders
        subfolders = [f for f in self.data["folders"]
                      if f.get("parent_id") == folder_id]
        for sub in subfolders:
            if delete_contents:
                # Remove subfolder contents mappings
                self._remove_folder_mappings(sub["id"])
                result["deleted_subfolders"].append(sub["id"])
            elif move_to_parent:
                # Move subfolder to parent (or root)
                sub["parent_id"] = parent_id
                # Also move its contents mappings stay with the subfolder
            else:
                # Move subfolder to root
                sub["parent_id"] = None

        # Handle this folder's direct contents
        if delete_contents:
            self._remove_folder_mappings(folder_id)
        else:
            # Move contents to parent (or root = unassigned)
            target = parent_id  # None means unassigned/root
            moved_chats, moved_files = self._move_folder_contents(
                folder_id, target
            )
            result["moved_chats"] = moved_chats
            result["moved_files"] = moved_files

        # Remove subfolders if deleting contents
        if delete_contents:
            self.data["folders"] = [
                f for f in self.data["folders"]
                if f.get("parent_id") != folder_id
            ]

        # Remove the folder itself
        self.data["folders"] = [
            f for f in self.data["folders"] if f["id"] != folder_id
        ]

        self._save()
        return result

    def _remove_folder_mappings(self, folder_id: str) -> None:
        """Remove all chat and file mappings for a folder."""
        self.data["chat_folder_map"] = {
            k: v for k, v in self.data["chat_folder_map"].items()
            if v != folder_id
        }
        self.data["file_folder_map"] = {
            k: v for k, v in self.data["file_folder_map"].items()
            if v != folder_id
        }

    def _move_folder_contents(self, from_folder_id: str,
                              to_folder_id: Optional[str]
                              ) -> tuple:
        """Move contents from one folder to another (or unassign if None)."""
        moved_chats = []
        moved_files = []

        for filename, fid in list(self.data["chat_folder_map"].items()):
            if fid == from_folder_id:
                if to_folder_id:
                    self.data["chat_folder_map"][filename] = to_folder_id
                else:
                    del self.data["chat_folder_map"][filename]
                moved_chats.append(filename)

        for filename, fid in list(self.data["file_folder_map"].items()):
            if fid == from_folder_id:
                if to_folder_id:
                    self.data["file_folder_map"][filename] = to_folder_id
                else:
                    del self.data["file_folder_map"][filename]
                moved_files.append(filename)

        return moved_chats, moved_files

    # ---- Tree ----

    def get_folder_tree(self) -> List[Dict[str, Any]]:
        """Return nested folder structure for UI."""
        root_folders = sorted(
            [f for f in self.data["folders"] if not f.get("parent_id")],
            key=lambda f: f.get("order", 0)
        )

        tree = []
        for root in root_folders:
            children = sorted(
                [f for f in self.data["folders"]
                 if f.get("parent_id") == root["id"]],
                key=lambda f: f.get("order", 0)
            )
            node = dict(root)
            node["children"] = children
            tree.append(node)

        return tree

    # ---- File/Chat assignments ----

    def assign_file_to_folder(self, filename: str,
                              folder_id: str) -> bool:
        """Assign a document to a folder."""
        if not self._find_folder(folder_id):
            return False
        self.data["file_folder_map"][filename] = folder_id
        self._save()
        return True

    def remove_file_from_folder(self, filename: str) -> bool:
        """Remove a file from its folder (unassign)."""
        if filename in self.data["file_folder_map"]:
            del self.data["file_folder_map"][filename]
            self._save()
            return True
        return False

    def assign_chat_to_folder(self, filename: str,
                              folder_id: str) -> bool:
        """Assign a chat to a folder."""
        if not self._find_folder(folder_id):
            return False
        self.data["chat_folder_map"][filename] = folder_id
        self._save()
        return True

    def remove_chat_from_folder(self, filename: str) -> bool:
        """Remove a chat from its folder (unassign)."""
        if filename in self.data["chat_folder_map"]:
            del self.data["chat_folder_map"][filename]
            self._save()
            return True
        return False

    def get_folder_contents(self, folder_id: str) -> Dict[str, List[str]]:
        """Get files and chats assigned to a folder."""
        chats = [fn for fn, fid in self.data["chat_folder_map"].items()
                 if fid == folder_id]
        files = [fn for fn, fid in self.data["file_folder_map"].items()
                 if fid == folder_id]
        return {"chats": chats, "files": files}

    def get_chat_folder(self, filename: str) -> Optional[str]:
        """Get the folder ID a chat is assigned to, or None."""
        return self.data["chat_folder_map"].get(filename)

    def get_file_folder(self, filename: str) -> Optional[str]:
        """Get the folder ID a file is assigned to, or None."""
        return self.data["file_folder_map"].get(filename)

    def get_all_mappings(self) -> Dict[str, Dict[str, str]]:
        """Return both maps for the UI."""
        return {
            "chat_folder_map": dict(self.data["chat_folder_map"]),
            "file_folder_map": dict(self.data["file_folder_map"]),
        }

    def reorder_folder(self, folder_id: str, new_order: int) -> bool:
        """Update a folder's sort order."""
        folder = self._find_folder(folder_id)
        if not folder:
            return False
        folder["order"] = new_order
        self._save()
        return True

    # ---- Prompt branch ----

    def _create_prompt_branch(self, folder_id: str, folder_name: str) -> str:
        """Create a prompt branch chat file for a folder. Returns the filename."""
        chat_id = str(uuid.uuid4())
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"folder_prompt_{folder_id[:8]}_{ts}.json"

        chat_data = {
            "chat_id": chat_id,
            "root_chat_id": chat_id,
            "parent_chat_id": "",
            "chat_history": [
                {
                    "role": "system",
                    "content": (
                        f"You are helping define a system prompt for the folder \"{folder_name}\". "
                        "When the user describes what they want, generate a well-structured "
                        "system prompt that defines the AI's role, knowledge, and behavior. "
                        "Refine it based on feedback."
                    ),
                    "timestamp": datetime.now().strftime("%H:%M"),
                }
            ],
            "conversation_summary": "",
            "token_count": 0,
            "title": f"[Prompt] {folder_name}",
        }

        path = os.path.join(self.chats_directory, fn)
        os.makedirs(self.chats_directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(chat_data, f, indent=2, ensure_ascii=False)

        # Assign prompt branch to the folder
        self.data["chat_folder_map"][fn] = folder_id
        return fn

    # ---- Saved prompts ----

    def save_prompt(self, folder_id: str, name: str,
                    content: str) -> Optional[Dict[str, Any]]:
        """Create a saved prompt entry. Returns the prompt dict or None."""
        folder = self._find_folder(folder_id)
        if not folder:
            return None
        folder.setdefault("saved_prompts", [])
        prompt = {
            "id": str(uuid.uuid4()),
            "name": name,
            "content": content,
            "tokens": len(content) // 4,  # rough estimate
            "created": self._now_iso(),
        }
        folder["saved_prompts"].append(prompt)
        # Auto-activate if it's the first saved prompt
        if len(folder["saved_prompts"]) == 1:
            folder["active_prompt_id"] = prompt["id"]
        self._save()
        return prompt

    def delete_prompt(self, folder_id: str, prompt_id: str) -> bool:
        """Remove a saved prompt by ID."""
        folder = self._find_folder(folder_id)
        if not folder:
            return False
        prompts = folder.get("saved_prompts", [])
        before = len(prompts)
        folder["saved_prompts"] = [p for p in prompts if p["id"] != prompt_id]
        if len(folder["saved_prompts"]) == before:
            return False
        # Clear active if deleted
        if folder.get("active_prompt_id") == prompt_id:
            folder["active_prompt_id"] = None
        self._save()
        return True

    def rename_prompt(self, folder_id: str, prompt_id: str,
                      new_name: str) -> bool:
        """Rename a saved prompt."""
        folder = self._find_folder(folder_id)
        if not folder:
            return False
        for p in folder.get("saved_prompts", []):
            if p["id"] == prompt_id:
                p["name"] = new_name
                self._save()
                return True
        return False

    def set_active_prompt(self, folder_id: str,
                          prompt_id: Optional[str]) -> bool:
        """Set which saved prompt is active (or None to disable)."""
        folder = self._find_folder(folder_id)
        if not folder:
            return False
        if prompt_id is not None:
            # Verify prompt exists
            if not any(p["id"] == prompt_id
                       for p in folder.get("saved_prompts", [])):
                return False
        folder["active_prompt_id"] = prompt_id
        self._save()
        return True

    def get_active_prompt_content(self, folder_id: str) -> str:
        """Return the content of the active saved prompt, or ''."""
        folder = self._find_folder(folder_id)
        if not folder:
            return ""
        active_id = folder.get("active_prompt_id")
        if not active_id:
            return ""
        for p in folder.get("saved_prompts", []):
            if p["id"] == active_id:
                return p.get("content", "")
        return ""

    def get_saved_prompts(self, folder_id: str) -> List[Dict[str, Any]]:
        """Return the list of saved prompts for a folder."""
        folder = self._find_folder(folder_id)
        if not folder:
            return []
        return list(folder.get("saved_prompts", []))

    def is_prompt_branch(self, filename: str) -> bool:
        """Check if a chat filename is a prompt branch for any folder."""
        for folder in self.data["folders"]:
            if folder.get("prompt_branch_filename") == filename:
                return True
        return False

    # ---- Folder memory ----

    def get_folder_memory(self, folder_id: str) -> Dict[str, Any]:
        """Return {notes} for a folder."""
        folder = self._find_folder(folder_id)
        if not folder:
            return {"notes": []}
        return {
            "notes": list(folder.get("memory_notes", [])),
        }

    def add_memory_note(self, folder_id: str, text: str,
                        source: str = "") -> bool:
        """Append a note to the folder's memory_notes."""
        folder = self._find_folder(folder_id)
        if not folder:
            return False
        folder.setdefault("memory_notes", []).append({
            "text": text,
            "source": source,
            "created": self._now_iso(),
        })
        self._save()
        return True

    def remove_memory_note(self, folder_id: str, index: int) -> bool:
        """Remove a memory note by index."""
        folder = self._find_folder(folder_id)
        if not folder:
            return False
        notes = folder.get("memory_notes", [])
        if 0 <= index < len(notes):
            notes.pop(index)
            self._save()
            return True
        return False

    def clear_memory(self, folder_id: str) -> bool:
        """Clear all memory notes."""
        folder = self._find_folder(folder_id)
        if not folder:
            return False
        folder["memory_notes"] = []
        self._save()
        return True
