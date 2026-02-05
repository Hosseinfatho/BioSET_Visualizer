"""
Biomni LLM API client using Gradio Client.
"""

from __future__ import annotations
import os
from typing import Optional
from gradio_client import Client


class BiomniClient:
    """Client for interacting with Biomni LLM via Gradio API."""
    
    def __init__(self):
        self.client: Optional[Client] = None
        self.authenticated = False
        self.biomni_url = "https://app.biomni.stanford.edu/app/"
        
    def login(self, username: Optional[str] = None, password: Optional[str] = None) -> bool:
        """
        Authenticate with Biomni using Gradio client.
        
        Args:
            username: Biomni username (falls back to env var BIOMNI_USERNAME)
            password: Biomni password (falls back to env var BIOMNI_PASSWORD)
            
        Returns:
            True if authentication successful
            
        Raises:
            ValueError: If credentials not provided
            Exception: If authentication fails
        """
        # Get credentials from args or environment
        username = username or os.getenv("BIOMNI_USERNAME")
        password = password or os.getenv("BIOMNI_PASSWORD")
        
        if not username or not password:
            raise ValueError(
                "Biomni credentials not provided. Set BIOMNI_USERNAME and "
                "BIOMNI_PASSWORD environment variables or pass them to login()."
            )
        
        try:
            print(f"[biomni] Connecting to {self.biomni_url}")
            self.client = Client(self.biomni_url)
            
            print(f"[biomni] Logging in as {username}")
            result = self.client.predict(
                username=username,
                password=password,
                api_name="/handle_login"
            )
            
            # handle_login returns tuple of 7 elements
            # We just need to check if login succeeded (client will handle session)
            self.authenticated = True
            print("[biomni] Login successful")
            return True
                
        except Exception as e:
            print(f"[biomni] Login failed: {e}")
            self.authenticated = False
            self.client = None
            raise
    
    def generate_response(
        self, 
        prompt: str, 
        model: str = "Claude-4-Sonnet",
        add_context: bool = True,
        state_info: dict = None
    ) -> str:
        """
        Generate a response from Biomni LLM.
        
        Args:
            prompt: User's question/prompt
            model: Model to use (default: Claude-4-Sonnet)
            add_context: Whether to add challenge context to prompt
            state_info: Current visualization state (channels, colors, settings, etc.)
            
        Returns:
            LLM response text
            
        Raises:
            RuntimeError: If not authenticated
            Exception: If API request fails
        """
        if not self.authenticated or self.client is None:
            raise RuntimeError("Not authenticated. Call login() first.")
        
        # Add context about the challenge and current visualization state to the prompt
        if add_context:
            prompt = self._add_challenge_context(prompt)
            if state_info:
                prompt += self._format_visualization_state(state_info)
        
        try:
            print(f"[biomni] Sending prompt to {model} (with state: {bool(state_info)})")
            
            # Call /process_input API with proper parameters
            result = self.client.predict(
                input_value={"text": prompt},
                inner_history=[],  # Executor chatbot history
                main_history=[],   # Co-pilot chatbot history
                model=model,
                direct_mode=True,  # Direct response mode for simpler output
                api_name="/process_input"
            )
            
            # result is tuple of 5 elements:
            # [0] inner_history (executor chatbot)
            # [1] main_history (co-pilot chatbot) 
            # [2] status markdown
            # [3] input value
            # [4] response markdown
            
            print(f"[biomni] Result type: {type(result)}, length: {len(result) if isinstance(result, (list, tuple)) else 'N/A'}")
            
            # Extract chat history to get assistant response
            main_history = result[1] if len(result) > 1 else []
            
            assistant_reply = ""
            if main_history and isinstance(main_history, list):
                # Find the last assistant message
                for msg in reversed(main_history):
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            assistant_reply = content
                            break
            
            # Fallback: try other result indices
            if not assistant_reply and len(result) > 4:
                assistant_reply = str(result[4]) if result[4] else ""
            
            if not assistant_reply:
                # Last resort: check all result elements for string content
                for item in result:
                    if isinstance(item, str) and len(item) > 0 and item not in ["", " "]:
                        assistant_reply = item
                        break
            
            if not assistant_reply:
                assistant_reply = "No response received from Biomni"
            
            print(f"[biomni] Response received ({len(assistant_reply)} chars)")
            return assistant_reply
            
        except Exception as e:
            import traceback
            print(f"[biomni] Generate response failed: {e}")
            traceback.print_exc()
            raise
    
    def _add_challenge_context(self, prompt: str) -> str:
        """Add BioMedVis challenge context to the prompt with system instructions."""
        context = (
            "You are an AI assistant specialized in biomedical imaging analysis, "
            "particularly for multiplexed tissue imaging data. "
            "You are helping researchers analyze a 3D microscopy imaging dataset as part of the "
            "Bio+MedVis Challenge at IEEE VIS 2025.\n\n"
            
            "CHALLENGE CONTEXT:\n"
            "Title: '3D Microscopy Imaging Challenge: From a RAW imaging volume to biological findings'\n\n"
            
            "Description: Highly multiplexed tissue imaging methods, such as Cyclic Immunofluorescence (CycIF), "
            "allow for the analysis of over 30 biomarkers on a single tissue section. These are essential tools "
            "for investigating the subcellular complexities of cancer. CyCIF has been instrumental in revealing "
            "immune-tumor interactions and the progression of melanoma at single-cell precision. "
            "Researchers have extended these techniques to image volumes, allowing for comprehensive analysis "
            "of diverse cell types and states within the tumor microenvironment and their spatial interactions.\n\n"
            
            "DATASET:\n"
            "The data volume represents a 194x5508x10908 volume of cancerous tissue from a patient with "
            "metastatic melanoma. Scientists have identified 'immune niches' in this tissue, which contain "
            "specific interactions between immune cells of different types and states.\n\n"
            
            "YOUR ROLE:\n"
            "- Help users understand what they're seeing in the visualization\n"
            "- Explain biological significance of marker combinations\n"
            "- Suggest interesting spatial interactions to explore\n"
            "- Provide context on what specific biomarkers indicate\n"
            "- Guide users through the visual analytics workflow\n\n"
            
            "INSTRUCTIONS:\n"
            "- Be concise and specific in your responses\n"
            "- Reference the current visualization state when relevant\n"
            "- DO NOT repeat this entire context in your answers\n"
            "- Focus on answering the user's specific question\n"
            "- Use domain expertise to provide biological insights\n\n"
            
            "USER'S QUESTION:\n"
        )
        return context + prompt

    def _format_visualization_state(self, state_info: dict) -> str:
        """Format current visualization state as context for the LLM."""
        if not state_info:
            return ""
        
        context_parts = ["\n\nCURRENT VISUALIZATION STATE:"]
        
        # Available channels in dataset
        if "available_channels" in state_info and state_info["available_channels"]:
            context_parts.append(f"Available biomarker channels in this dataset: {', '.join(state_info['available_channels'])}")
    
        # Active channels
        if "active_channels" in state_info and state_info["active_channels"]:
            context_parts.append(f"Active channels being displayed: {', '.join(state_info['active_channels'])}")
        
        # Channel colors
        if "channel_colors" in state_info and state_info["channel_colors"]:
            color_info = [f"{name} ({color})" for name, color in state_info["channel_colors"].items()]
            context_parts.append(f"Channel colors: {', '.join(color_info)}")
        
        # Analysis settings
        if "dilation" in state_info:
            context_parts.append(f"Current dilation: {state_info['dilation']} μm")
        
        if "hierarchy_level" in state_info:
            level_name = {0: "Fine", 1: "Medium", 2: "Coarse"}.get(state_info["hierarchy_level"], str(state_info["hierarchy_level"]))
            context_parts.append(f"Detail level: {level_name}")
        
        # Heatmap info
        if "heatmap_tile_count" in state_info and state_info["heatmap_tile_count"] > 0:
            context_parts.append(f"Heatmap showing {state_info['heatmap_tile_count']} tiles with marker co-localization")
        
        # Data loaded info
        if "data_loaded" in state_info and state_info["data_loaded"]:
            if "total_channels" in state_info:
                context_parts.append(f"Dataset loaded with {state_info['total_channels']} total available channels")
        
        return "\n".join(context_parts) if len(context_parts) > 1 else ""
    
    def logout(self):
        """Clear authentication state."""
        self.authenticated = False
        self.client = None
        print("[biomni] Logged out")