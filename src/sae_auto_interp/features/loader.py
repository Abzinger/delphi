from tqdm import tqdm
from typing import List, Dict, NamedTuple, Callable

import torch
import torch.multiprocessing as mp
from torchtyping import TensorType
from safetensors.torch import load_file

from .transforms import Transform
from ..config import FeatureConfig
from ..features.features import Feature, FeatureRecord

CONFIG = FeatureConfig()


class BufferOutput(NamedTuple):
    feature: Feature

    locations: TensorType["locations", 2]

    activations: TensorType["locations"]


class TensorBuffer:
    """
    Lazy loading buffer for cached splits.
    """

    def __init__(self, path, module_path, features):
        self.tensor_path = path
        self.module_path = module_path

        self.features = features
        self.start = 0

        self.activations = None
        self.locations = None

    def _load(self):

        split_data = load_file(self.tensor_path)

        self.activations = split_data["activations"]
        self.locations = split_data["locations"]

    def __iter__(self):
        self._load()

        if self.features is None:
            self.features = torch.unique(self.locations[:, 2])

        return self
    
    def __next__(self):

        if self.start >= len(self.features):
            raise StopIteration
        
        feature = self.features[self.start]

        mask = self.locations[:, 2] == feature

        # NOTE: MIN examples is here
        if mask.sum() <= 120:
            self.start += 1
            return None
        
        feature_locations = self.locations[mask][:,:2]
        feature_activations = self.activations[mask]

        self.start += 1

        return BufferOutput(
            Feature(
                self.module_path,
                feature.item()
            ),
            feature_locations,
            feature_activations
        )


class FeatureDataset:
    """
    Dataset which constructs TensorBuffers for each module and feature.
    """

    def __init__(
        self, 
        raw_dir: str,
        modules: List[str],
        features: Dict[str, int] = None,
        cfg: FeatureConfig = CONFIG,
    ):
        self.cfg = cfg

        self.buffers = []

        if features is None:

            self._load_all(raw_dir, modules)

        else:
        
            self._load_selected(
                raw_dir,
                modules, 
                features
            )

    def _edges(self):

        return torch.linspace(
            0, 
            self.cfg.width, 
            steps=self.cfg.n_splits+1
        ).long()
    
    def _load_all(self, raw_dir: str, modules: List[str]):
        """
        Build dataset buffers which load all cached features. 
        """

        edges = self._edges()

        for module in modules:
            for start, end in zip(edges[:-1], edges[1:]):

                # Adjust end by one as the path avoids overlap
                path = f"{raw_dir}/{module}/{start}_{end-1}.safetensors"

                self.buffers.append(
                    TensorBuffer(
                        path, 
                        module,
                        None
                    )
                )

    def _load_selected(self, raw_dir: str, modules: List[str], features: Dict[str, int]):
        """
        Build a dataset buffer which loads only selected features.
        """
        
        edges = self._edges()
        
        for module in modules:

            selected_features = features[module]

            bucketized = torch.bucketize(selected_features, edges, right=True)
            unique_buckets = torch.unique(bucketized)

            for bucket in unique_buckets:
                mask = bucketized == bucket

                _selected_features = selected_features[mask]

                start, end = edges[bucket-1], edges[bucket]

                # Adjust end by one as the path avoids overlap
                path = f"{raw_dir}/{module}/{start}_{end-1}.pt"

                self.buffers.append(
                    TensorBuffer(
                        path, 
                        module,
                        _selected_features
                    )
                )


class FeatureLoader:
    """
    Loader which applies transformations and samplers to data.
    """

    def __init__(
        self,
        tokens: TensorType["batch", "seq"],
        dataset: FeatureDataset,
        constructor: Callable = None,
        sampler: Callable = None,
        transform: Transform = None,
    ):
        """
        Args:
            tokens (TensorType["batch", "seq"]): The tokenized input data.
            dataset (FeatureDataset): The dataset to load.
            constructor (Callable): A function defining how examples are sampled from the tokens.
            sampler (Callable): A function for sampling top examples into train/test splits.
            transform (Transform): A transform for adding information to the FeatureRecord.
        """
        self.tokens = tokens
        self.dataset = dataset

        self.constructor = constructor
        self.sampler = sampler
        self.transform = transform

    def __len__(self):

        return len(self.dataset.buffers)

    def _process(self, data: BufferOutput):
        record = FeatureRecord(data.feature)

        if self.constructor:
            self.constructor(
                record,
                self.tokens,
                locations = data.locations,
                activations = data.activations,
            )

        if self.sampler is not None:
            self.sampler(
                record
            )

        # if self.transform is not None:
        #     self.transform(
        #         record
        #     )

        return record
    
    def load(self, collate: bool = False, num_workers: int = 2):

        return self._all(num_workers=num_workers) \
            if collate else self._batched()
    
    def _worker(self, buffer):
        """
        Worker for loading all features from a buffer.
        """
        return [
            self._process(data)
            for data in tqdm(buffer, desc=f"Loading {buffer.module_path}")
            if data is not None
        ]

    def _all(self, num_workers: int):
        with mp.Pool(processes=num_workers) as pool:
            all_records = pool.map(self._process_buffer, self.dataset.buffers)
        
        return sum(all_records, [])
    
    def _batched(self):

        for buffer in self.dataset.buffers:

            yield self._worker(buffer)