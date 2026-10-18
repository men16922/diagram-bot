"""Synthetic reference architectures following AWS best-practice patterns. Never live data.

Each sample uses only fields the collector reads from the AWS APIs, so it renders exactly like a
collected snapshot would.
"""
from .builder import ACCOUNT, DETAILS, LISTINGS, OPTIONAL, RAW_KEYS, REGION, SAMPLE_VERSION, Sample
from .catalog import (SAMPLES, SUMMARIES, ai_platform, data_platform, eks_platform, environments, genai_rag, gpu_training, hybrid,
                      microservices, multi_account, static_ip_front, three_tier)

__all__ = ["ACCOUNT", "DETAILS", "LISTINGS", "OPTIONAL", "RAW_KEYS", "REGION", "SAMPLE_VERSION", "SAMPLES", "SUMMARIES", "Sample",
           "ai_platform", "data_platform", "eks_platform", "environments", "genai_rag", "gpu_training", "hybrid", "microservices",
           "multi_account", "static_ip_front", "three_tier"]
