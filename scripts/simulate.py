import asyncio
import orjson

import torch

from sae_auto_interp.explainers import explanation_loader
from sae_auto_interp.scorers import OpenAISimulator
from sae_auto_interp.clients import Outlines
from sae_auto_interp.utils import load_tokenized_data, load_tokenizer, default_constructor
from sae_auto_interp.features import top_and_quantiles, FeatureLoader, FeatureDataset
from sae_auto_interp.sequential_pipeline import Pipe, Pipeline, Actor
from functools import partial
from sae_auto_interp.config import FeatureConfig

### Set directories ###

RAW_FEATURES_PATH = "raw_features/gpt2"
EXPLAINER_OUT_DIR = "results/explanations/simple"
SCORER_OUT_DIR = "results/simulation"

### Load dataset ###

tokenizer = load_tokenizer('gpt2')
tokens = load_tokenized_data(tokenizer)

modules = [".transformer.h.0", ".transformer.h.2"]
features = {
    m : torch.arange(1) for m in modules
}

dataset = FeatureDataset(
    raw_dir=RAW_FEATURES_PATH,
    modules = modules,
    cfg=FeatureConfig(),
    features=features,
)

loader = FeatureLoader(
    tokens=tokens,
    dataset=dataset,
    constructor=default_constructor,
    sampler=top_and_quantiles
)

### Load client ###

client = Outlines("meta-llama/Meta-Llama-3-8B-Instruct")

### Build Explainer pipe ###

explainer_pipe = Pipe(
    Actor(
        partial(explanation_loader, explanation_dir=EXPLAINER_OUT_DIR)
    )
)

### Build Scorer pipe ###

def scorer_preprocess(result):
    record = result.record
    record.explanation = result.explanation
    record.test = record.test[0][:5]
    return record

def scorer_postprocess(result):
    result = result.result()
    with open(f"{SCORER_OUT_DIR}/{result.record.feature}.txt", "wb") as f:
        f.write(orjson.dumps(result.score))

scorer_pipe = Pipe(
    Actor(
        OpenAISimulator(client, tokenizer=tokenizer),
        preprocess=scorer_preprocess,
        postprocess=scorer_postprocess
    )
)

### Build the pipeline ###

pipeline = Pipeline(
    loader.load,
    explainer_pipe,
    scorer_pipe,
)

asyncio.run(
    pipeline.run()
)

