---
name: Add Google Search Grounding to Gemini/Vertex AI
overview: Enable native Google Search capabilities in Gemini and Vertex AI LLM services to answer real-time web queries with integrated citations.
todos:
  - id: add-component-type
    content: Add CITATIONS to ComponentType enum
    status: completed
  - id: create-citations-component
    content: Implement CitationsComponent rich component
    status: completed
  - id: create-google-search-tool
    content: Implement GoogleSearchTool definition
    status: completed
  - id: update-gemini-integration
    content: Integrate search grounding in GeminiLlmService
    status: completed
  - id: update-vertex-integration
    content: Integrate search grounding in VertexAILlmService
    status: completed
  - id: update-agent-logic
    content: Extract and yield citations in Agent logic
    status: completed
---

