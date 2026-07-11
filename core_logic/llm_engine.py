"""
LLM Engine - Language Model Integration

Supports multiple backends: Ollama (local), Gemini, Groq, OpenAI
Includes streaming support and conversation context management.
"""

import logging
import socket
from typing import Optional, List, Dict, Any, Generator
import requests

from core_logic.config import Config
from core_logic.error_handler import LLMException, retry_on_failure
from core_logic.system_prompt import get_system_prompt

logger = logging.getLogger(__name__)


class LLMEngine:
    """Base LLM interface supporting multiple backends."""
    
    def __init__(self, backend: Optional[str] = None):
        """
        Initialize LLM engine.
        
        Args:
            backend: LLM backend ("ollama", "gemini", "groq", "openai")
                    If None, uses Config.LLM_BACKEND
        """
        self.system_prompt = get_system_prompt()
        
        # Determine backend based on network availability
        if self._is_online():
            self.backend = "groq"
            logger.info("🌐 Network detected — automatically shifting to Groq")
        else:
            self.backend = "ollama"
            logger.info("📴 Offline mode detected — automatically shifting to Ollama")
        
        logger.info(f"Initializing LLM engine: {self.backend}")
        self._validate_backend()
        
    def _is_online(self) -> bool:
        """Check if we have an active internet connection by pinging a reliable DNS."""
        try:
            # 8.8.8.8 on port 53 (DNS) is extremely reliable and fast to check
            socket.create_connection(("8.8.8.8", 53), timeout=1.5)
            return True
        except OSError:
            pass
        return False
    
    def _validate_backend(self) -> None:
        """Validate backend configuration."""
        if self.backend == "ollama":
            # Test Ollama connection
            try:
                response = requests.get(
                    f"{Config.OLLAMA_BASE_URL}/api/tags",
                    timeout=5
                )
                if response.status_code == 200:
                    logger.info(f"✅ Connected to Ollama at {Config.OLLAMA_BASE_URL}")
                else:
                    raise LLMException(f"Ollama returned {response.status_code}")
            except Exception as e:
                raise LLMException(f"Cannot connect to Ollama: {str(e)}")
        
        elif self.backend == "gemini":
            if not Config.GEMINI_API_KEY:
                raise LLMException("GEMINI_API_KEY not set in .env")
            logger.info("✅ Gemini API configured")
        
        elif self.backend == "groq":
            if not Config.GROQ_API_KEY:
                raise LLMException("GROQ_API_KEY not set in .env")
            logger.info("✅ Groq API configured")
        
        elif self.backend == "openai":
            if not Config.OPENAI_API_KEY:
                raise LLMException("OPENAI_API_KEY not set in .env")
            logger.info("✅ OpenAI API configured")
        
        else:
            raise LLMException(f"Unknown LLM backend: {self.backend}")
    
    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        context: Optional[List[Dict[str, str]]] = None,
        stream: bool = False
    ) -> Any:
        """
        Generate response using LLM.
        
        Args:
            prompt: User prompt/query
            temperature: Randomness (0.0 = deterministic, 1.0 = creative)
            max_tokens: Maximum response length
            system_prompt: Custom system prompt (overrides default)
            context: Conversation history in OpenAI format
            stream: If True, return generator for streaming response
            
        Returns:
            LLM response text (or generator if stream=True)
        """
        sys_prompt = system_prompt or self.system_prompt
        
        if self.backend == "ollama":
            return self._generate_ollama(prompt, temperature, max_tokens, sys_prompt, context, stream)
        elif self.backend == "gemini":
            return self._generate_gemini(prompt, temperature, max_tokens, sys_prompt, context, stream)
        elif self.backend == "groq":
            return self._generate_groq(prompt, temperature, max_tokens, sys_prompt, context, stream)
        elif self.backend == "openai":
            return self._generate_openai(prompt, temperature, max_tokens, sys_prompt, context, stream)
        else:
            raise LLMException(f"Unknown backend: {self.backend}")
    
    def _generate_ollama(
        self,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
        system_prompt: str,
        context: Optional[List[Dict[str, str]]] = None,
        stream: bool = False
    ) -> Any:
        """Generate using Ollama (local).

        Uses the /api/chat endpoint so multi-turn ``context`` is actually
        respected — important for follow-ups like "and tomorrow?" that depend
        on the previous question.
        """
        try:
            logger.debug(f"Calling Ollama: {Config.OLLAMA_MODEL}")

            messages: List[Dict[str, str]] = [
                {"role": "system", "content": system_prompt}
            ]
            if context:
                for turn in context:
                    role = turn.get("role")
                    content = (turn.get("content") or "").strip()
                    if role in ("user", "assistant") and content:
                        messages.append({"role": role, "content": content})
            messages.append({"role": "user", "content": prompt})

            options: Dict[str, Any] = {"temperature": temperature}
            if max_tokens:
                # Ollama uses num_predict for max output tokens.
                options["num_predict"] = int(max_tokens)

            response = requests.post(
                f"{Config.OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": Config.OLLAMA_MODEL,
                    "messages": messages,
                    "options": options,
                    "stream": stream,
                },
                timeout=60,
                stream=stream,
            )

            if response.status_code != 200:
                raise LLMException(f"Ollama error: {response.text}")

            if stream:
                return self._stream_ollama_chat_response(response)

            result = response.json()
            # /api/chat returns {"message": {"role": "assistant", "content": "..."}}
            text = (result.get("message") or {}).get("content", "").strip()
            logger.debug(f"Ollama response: {text[:100]}...")
            return text

        except Exception as e:
            logger.error(f"Ollama generation failed: {str(e)}")
            raise LLMException(f"Ollama failed: {str(e)}")

    @staticmethod
    def _stream_ollama_chat_response(response) -> Generator[str, None, None]:
        """Stream tokens from Ollama /api/chat response."""
        try:
            import json as _json
            for line in response.iter_lines():
                if not line:
                    continue
                data = _json.loads(line)
                token = (data.get("message") or {}).get("content", "")
                if token:
                    yield token
        except Exception as e:
            logger.error(f"Streaming error: {str(e)}")
    
    @staticmethod
    def _stream_ollama_response(response) -> Generator[str, None, None]:
        """Stream tokens from Ollama response."""
        try:
            for line in response.iter_lines():
                if line:
                    import json
                    data = json.loads(line)
                    token = data.get("response", "")
                    if token:
                        yield token
        except Exception as e:
            logger.error(f"Streaming error: {str(e)}")
    
    @staticmethod
    def _stream_chunked_response(response) -> Generator[str, None, None]:
        """Stream tokens from OpenAI/Groq chunked API response."""
        try:
            for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            logger.error(f"Streaming error: {str(e)}")
    
    def _generate_gemini(
        self,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
        system_prompt: str,
        context: Optional[List[Dict[str, str]]] = None,
        stream: bool = False
    ) -> Any:
        """Generate using Google Gemini API."""
        try:
            import google.generativeai as genai
            
            genai.configure(api_key=Config.GEMINI_API_KEY)
            model = genai.GenerativeModel(Config.GEMINI_MODEL)
            
            full_prompt = f"{system_prompt}\n\n"
            if context:
                for turn in context:
                    role = "User" if turn["role"] == "user" else "Assistant"
                    full_prompt += f"{role}: {turn['content']}\n"
            full_prompt += f"User: {prompt}"
            
            response = model.generate_content(
                full_prompt,
                generation_config={
                    "temperature": temperature,
                    "max_output_tokens": max_tokens or 1000
                }
            )
            
            text = response.text.strip()
            logger.debug(f"Gemini response: {text[:100]}...")
            return text
            
        except Exception as e:
            logger.error(f"Gemini generation failed: {str(e)}")
            raise LLMException(f"Gemini failed: {str(e)}")
    
    def _generate_groq(
        self,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
        system_prompt: str,
        context: Optional[List[Dict[str, str]]] = None,
        stream: bool = False
    ) -> Any:
        """Generate using Groq API."""
        try:
            from groq import Groq
            
            client = Groq(api_key=Config.GROQ_API_KEY)
            
            messages = [{"role": "system", "content": system_prompt}]
            if context:
                for turn in context:
                    messages.append({"role": turn["role"], "content": turn["content"]})
            messages.append({"role": "user", "content": prompt})
            
            response = client.chat.completions.create(
                model=Config.GROQ_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens or 1000,
                stream=stream
            )
            
            if stream:
                return self._stream_chunked_response(response)
            
            text = response.choices[0].message.content.strip()
            logger.debug(f"Groq response: {text[:100]}...")
            return text
            
        except Exception as e:
            logger.error(f"Groq generation failed: {str(e)}")
            raise LLMException(f"Groq failed: {str(e)}")
    
    def _generate_openai(
        self,
        prompt: str,
        temperature: float,
        max_tokens: Optional[int],
        system_prompt: str,
        context: Optional[List[Dict[str, str]]] = None,
        stream: bool = False
    ) -> Any:
        """Generate using OpenAI API."""
        try:
            from openai import OpenAI
            
            client = OpenAI(api_key=Config.OPENAI_API_KEY)
            
            messages = [{"role": "system", "content": system_prompt}]
            if context:
                for turn in context:
                    messages.append({"role": turn["role"], "content": turn["content"]})
            messages.append({"role": "user", "content": prompt})
            
            response = client.chat.completions.create(
                model=Config.OPENAI_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens or 1000,
                stream=stream
            )
            
            if stream:
                return self._stream_chunked_response(response)
            
            text = response.choices[0].message.content.strip()
            logger.debug(f"OpenAI response: {text[:100]}...")
            return text
            
        except Exception as e:
            logger.error(f"OpenAI generation failed: {str(e)}")
            raise LLMException(f"OpenAI failed: {str(e)}")
    
    def get_info(self) -> Dict[str, Any]:
        """Get LLM engine information."""
        return {
            "backend": self.backend,
            "model": {
                "ollama": Config.OLLAMA_MODEL,
                "groq": Config.GROQ_MODEL,
                "gemini": Config.GEMINI_MODEL,
                "openai": Config.OPENAI_MODEL,
            }.get(self.backend, self.backend),
            "type": "LLM Engine",
            "system_prompt_length": len(self.system_prompt)
        }
