import yaml
import logging 
import numpy as np
from pathlib import Path
from pydantic import validate_call
from typing import Literal


from .predict import predict_windows
from .pretrained import download_pretrained
from .model import TrackingTransformer
from ..data import build_windows, get_features
from ..tracking import build_graph, track_greedy

logger = logging.getLogger(__name__)

class Trackastra:
    def __init__(self, transformer, train_args, device="cpu"):
        # Hack: to(device) for some more submodules that map_location does cover
        self.transformer = transformer.to(device)
        self.train_args = train_args
        self.device = device

    @classmethod
    @validate_call
    def load_from_folder(cls, dir: Path, device: str = "cpu"):
        transformer = TrackingTransformer.from_folder(dir, map_location=device)
        train_args = yaml.load(open(dir / "train_config.yaml"), Loader=yaml.FullLoader)
        return cls(transformer=transformer, train_args=train_args, device=device)

    # TODO make safer
    @classmethod
    @validate_call
    def load_pretrained(
        cls, name: str, device: str = "cpu", download_dir: Path = "./.models"
    ):
        download_pretrained(name, download_dir)
        # download zip from github to location/name, then unzip
        return cls.load_from_folder(dir=Path(download_dir) / name, device=device)

    def _predict(
        self, imgs: np.ndarray, masks: np.ndarray, edge_threshold: float = 0.05, n_workers: int = 8
    ):
        logger.info("Predicting weights for candidate graph")
        self.transformer.eval()
        
        features = get_features(
            detections=masks, imgs=imgs, ndim=self.transformer.config["coord_dim"], n_workers=n_workers
        )
        windows = build_windows(features, window_size=self.transformer.config["window"])
        
        predictions = predict_windows(
            windows=windows,
            features=features,
            model=self.transformer,
            edge_threshold=edge_threshold,
            spatial_dim=masks.ndim - 1,
        )

        return predictions

    def _track_from_predictions(
        self,
        predictions,
        mode: Literal["greedy", "ilp"] = "greedy",
        use_distance: bool = False,
        max_distance: int = 256,
        max_neighbors: int = 10,
        delta_t: int = 1,
        **kwargs,
    ):

        logger.info("Running greedy tracker")
        nodes = predictions["nodes"]
        weights = predictions["weights"]
        
        candidate_graph = build_graph(
            nodes=nodes,
            weights=weights,
            use_distance=use_distance,
            max_distance=max_distance,
            max_neighbors=max_neighbors,
            delta_t=delta_t,
        )
        if mode == "greedy":
            return track_greedy(candidate_graph)
        elif mode == "ilp":
            from trackastra.tracking.ilp import track_ilp

            return track_ilp(candidate_graph, **kwargs)
        else:
            raise ValueError(f"Tracking mode {mode} does not exist.")

    def track(
        self,
        imgs: np.ndarray,
        masks: np.ndarray,
        mode: Literal["greedy", "ilp"] = "greedy",
        **kwargs,
    ):
        predictions = self._predict(imgs, masks)
        track_graph = self._track_from_predictions(predictions, mode=mode, **kwargs)
        return track_graph