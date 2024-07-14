from .client import Client
from ..logger import logger
from asyncio import sleep
import json

from openai import AsyncOpenAI

class Local(Client):
    provider = "vllm"

    def __init__(self,
        model: str, 
        base_url="http://localhost:8000/v1"
    ):
        super().__init__(model)
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key="EMPTY",
            timeout=None
        )
        self.model = model

    async def generate(
        self, 
        prompt: str, 
        raw: bool = False,
        max_retries: int = 3,
        **kwargs
    ) -> str:
        """
        Wrapper method for vLLM post requests.
        """
        
        for attempt in range(max_retries):

            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=prompt,
                    **kwargs
                )
                
                if raw:
                    return response
                
                return self.postprocess(response)
            
            except json.JSONDecodeError:
                logger.warning(f"Attempt {attempt + 1}: Invalid JSON response, retrying...")
            
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}: {str(e)}, retrying...")
            
            await sleep(1)

        logger.error("All retry attempts failed.")
        raise RuntimeError("Failed to generate text after multiple attempts.")
    
    def postprocess(self, response: dict) -> str:
        """
        Postprocess the response from the API.
        """
        return response.choices[0].message.content