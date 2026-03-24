"""
adapters — Log format adapters for LLM Failure Atlas.

Convert raw agent logs into matcher-compatible telemetry.

Available adapters:
  - LangChainAdapter: LangChain trace JSON
  - LangSmithAdapter: LangSmith run-tree export
  - AtlasCallbackHandler: LangChain/LangGraph real-time callback
  - watch(): LangGraphics-style wrapper for compiled graphs
  - CrewAIAdapter: CrewAI post-hoc from CrewOutput
  - AtlasCrewListener: CrewAI real-time event listener
"""