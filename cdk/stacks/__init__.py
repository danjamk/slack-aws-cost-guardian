"""CDK stacks for Slack AWS Cost Guardian."""

from cdk.stacks.storage_stack import StorageStack
from cdk.stacks.collector_stack import CollectorStack

__all__ = ["StorageStack", "CollectorStack"]