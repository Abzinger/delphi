import asyncio
import json
import random
import re
from abc import abstractmethod

import numpy as np
from transformers import PreTrainedTokenizer

from ...clients.client import Client
from ...features import FeatureRecord
from ...logger import logger
from ..scorer import Scorer, ScorerResult
from .sample import ClassifierOutput, Sample


class Classifier(Scorer):
    def __init__(
        self,
        client: Client,
        tokenizer: PreTrainedTokenizer,
        verbose: bool,
        batch_size: int,
        log_prob: bool,
        **generation_kwargs,
    ):
        self.client = client
        self.tokenizer = tokenizer
        self.verbose = verbose

        self.batch_size = batch_size
        self.generation_kwargs = generation_kwargs
        self.log_prob = log_prob



    async def __call__(
        self,
        record: FeatureRecord,
    ) -> list[ClassifierOutput]:
        samples = self._prepare(record)

        random.shuffle(samples)
        samples = self._batch(samples)
        results = await self._query(
            record.explanation,
            samples,
        )
        
        return ScorerResult(record=record, score=results)

    @abstractmethod
    def _prepare(self, record: FeatureRecord) -> list[list[Sample]]:
        pass


    async def _query(
        self,
        explanation: str,
        batches: list[list[Sample]],
    ) -> list[ClassifierOutput]:
        """
        Send and gather batches of samples to the model.
        """
        sem = asyncio.Semaphore(1)

        async def _process(explanation, batch):
            async with sem:
                result = await self._generate(explanation, batch)
                return result
    
        tasks = [asyncio.create_task(_process(explanation, batch)) for batch in batches]
        results = await asyncio.gather(*tasks)

        return sum(results, [])
    

    async def _generate(
        self, explanation: str, batch: list[Sample]
    ) -> list[ClassifierOutput]:
        """
        Generate predictions for a batch of samples.
        """

        prompt = self._build_prompt(explanation, batch)
        if self.log_prob:
            self.generation_kwargs["logprobs"] = True
            self.generation_kwargs["top_logprobs"] = 5
        try:
            response = await self.client.generate(prompt, **self.generation_kwargs)
        except Exception as e:
            logger.error(f"Error generating text: {e}")
            response = None
        if response is None:
            predictions = [-1] * self.batch_size
            probabilities = [-1] * self.batch_size
        else:
            selections = response.text
            logprobs = response.logprobs if self.log_prob else None
            try:
                predictions, probabilities = self._parse(selections, logprobs)
            except Exception as e:
                logger.error(f"Parsing selections failed: {e}")
                predictions = [-1] * self.batch_size
                probabilities = [-1] * self.batch_size

        results = []
        correct = []
        response = []

        for sample, prediction, probability in zip(batch, predictions, probabilities):
            result = sample.data
            result.prediction = prediction
            result.correct = prediction == result.ground_truth
            correct.append(result.ground_truth)
            response.append(prediction)
            if probability is not None:
                result.probability = probability
            results.append(result)

            if self.verbose:
                result.text = sample.text
        return results

    
    def _parse(self, string, logprobs=None):
        """Extract binary predictions and probabilities from a string and 
        optionally its token logprobs."""
        # Matches the first instance of text enclosed in square brackets
        pattern = r"\[.*?\]"
        match = re.search(pattern, string)

        predictions: list[int] = json.loads(match.group(0))
        assert len(predictions) == self.batch_size
        probabilities = (
            self._parse_logprobs(logprobs)
            if logprobs is not None
            else [None] * self.batch_size
        )

        return predictions, probabilities


    def _parse_logprobs(self, logprobs: list):
        """
        Extracts normalized probabilities of '1' vs '0' tokens from the top n log probabilities for each 
        token in a response string of form '[x, x, x, ...]'. The normalized probability is computed as 
        P(1)/(P(0) + P(1)), where P(0) and P(1) are summed over all matching tokens in the top 5 candidates.

        Args:
            logprobs (list): Contains top n log probabilities for each token in the response.

        Returns:
            list: Normalized probabilities between 0 and 1, where each value represents P(token='1')."""
        binary_probabilities: list[float] = []
        
        for i in range(len(logprobs)):
            if "1" in logprobs[i].token or "0" in logprobs[i].token:
                top_logprobs = logprobs[i].top_logprobs
                prob_0 = 0.
                prob_1 = 0.
                for i in range(len(top_logprobs)):
                    token = top_logprobs[i].token
                    logprob = top_logprobs[i].logprob
                    if "0" in token:
                        prob_0 += np.exp(logprob).item()
                    elif "1" in token:
                        prob_1 += np.exp(logprob).item()
                if prob_0 + prob_1 > 0:
                    binary_probabilities.append(prob_1 / (prob_0 + prob_1))
                else:
                    binary_probabilities.append(0.)

        assert len(binary_probabilities) == self.batch_size
        return binary_probabilities


    def _build_prompt(
        self,
        explanation: str,
        batch: list[Sample],
    ) -> str:
        """
        Prepare prompt for generation.
        """

        examples = "\n".join(
            f"Example {i}: {sample.text}" for i, sample in enumerate(batch)
        )
        
        return self.prompt(explanation=explanation, examples=examples)

    def _batch(self, samples):
        return [
            samples[i : i + self.batch_size]
            for i in range(0, len(samples), self.batch_size)
        ]

    def call_sync(self, record: FeatureRecord) -> list[ClassifierOutput]:
        return asyncio.run(self.__call__(record))
