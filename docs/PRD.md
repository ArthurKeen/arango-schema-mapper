Based on your requirements and the uploaded documents, I have designed a comprehensive **Product Requirements Document (PRD)** and **Implementation Plan** for the **Agentic Hybrid Schema Analyzer**.

This system unifies your existing work on `arango-cypher`, `arango-sparql`, and `arango-sql` by creating a shared semantic understanding of the underlying physical data, regardless of whether it is stored as RDF, LPG, PG, or a hybrid of these.

---

# Part 1: Product Requirements Document (PRD)

## **Project Name:** Agentic Hybrid Schema Analyzer

### **1. Executive Summary**

The **Agentic Hybrid Schema Analyzer** is a standalone library that uses algorithmic heuristics and Large Language Models (LLMs) to reverse-engineer a semantic **Conceptual Model (OWL Ontology)** from an existing ArangoDB **Physical Schema**. It simultaneously generates a machine-readable **Mapping Layer** that links the conceptual entities to their physical implementations. This enables language transpilers (Cypher, SPARQL, SQL) to generate correct AQL for hybrid schemas that mix RDF, LPG, and Property Graph patterns.

### **2. Problem Statement**

ArangoDB schemas "in the wild" are often hybrids:

* **Pure Graph (RDF-style):** Data in edge collections; types defined by edges to Class nodes.
* **Property Graph (PG-style):** Typed vertex/edge collections (e.g., `Users`, `Follows`).
* **Labeled Property Graph (LPG-style):** Generic collections with `type` attributes (e.g., Neo4j imports).
* **Optimized Hybrids:** PG schemas with **Vertex-Centric Indexing (VCI)** (denormalized properties on edges) or GraphRAG structures (LPG data inside PG collections).

Current transpilers rely on rigid assumptions. Without a unified map of *how* a concept is implemented, transpilers cannot generate optimized AQL for these hybrid scenarios.

### **3. Functional Requirements**

#### **3.1. Schema Reverse Engineering**

The system must analyze an ArangoDB database and produce two artifacts:

1. **Conceptual Schema (OWL 2 DL):** A standard ontology defining Classes, ObjectProperties (relationships), and DatatypeProperties (attributes).
2. **Physical Mapping (RDF Annotations):** Metadata decorating the ontology that describes exactly how each OWL entity maps to ArangoDB collections, filters, or graph traversals.

#### **3.2. Hybrid Pattern Detection**

The system must automatically detect and classify schema fragments into:

* **RPT (RDF Topology):** Detect `_triples` collections or graph structures using `rdf:type` edges.
* **PGT (Property Graph Topology):** Detect typed vertex/edge collections based on naming conventions and graph definitions.
* **LPG (Labeled Property Graph):** Detect generic node collections using discriminator fields (e.g., `type`, `label`).
* **GraphRAG / Hybrid:** Detect specific templates, such as "LPG data stored in PG collections" or VCI optimization patterns.

#### **3.3. Agentic Semantic Enrichment**

Where heuristics fail, the system must use an LLM to:

* **Infer Semantics:** Map cryptic physical names (e.g., `c_102`) to semantic concepts (e.g., `Customer`) based on property analysis.
* **Resolve Ambiguity:** Determine if a generic edge collection represents a specific relationship type based on the types of nodes it connects.

#### **3.4. Output Specification**

The output must be a serialized OWL ontology (Turtle/JSON-LD) containing custom mapping annotations:

* `phys:collectionName`: The physical collection.
* `phys:mappingStyle`: `COLLECTION`, `LABEL`, `TRIPLE`, or `VCI`.
* `phys:typeField`: The attribute used for filtering (if LPG).
* `phys:vciField`: The property on an edge that duplicates a vertex property.

---

# Part 2: Implementation Plan

This plan integrates your existing detection logic into a multi-stage pipeline.

### **Phase 1: Architecture & Core Heuristics (Weeks 1-2)**

**Goal:** Consolidate existing detection logic into a unified `PhysicalIntrospector`.

1. **Create `PhysicalIntrospector` Class:**
* Merge logic from `model-detector.js` (RDF/RPT detection) and `graph-model-detector.js` (LPG/PG detection).
* **Input:** Database handle.
* **Output:** `PhysicalSchemaMetadata` JSON (collections, edge definitions, index definitions, sample documents).


2. **Implement Template Matcher:**
* Create a library of "Schema Templates" to recognize known patterns before calling the LLM.
* **Template A (Pure PG):** Distinct vertex collections, no discriminator fields.
* **Template B (Pure LPG):** Single vertex/edge collections with `label`/`type` fields.
* **Template C (GraphRAG):** Specific hybrid style (e.g., text chunks in one collection, entities in another, connected by similarity edges).
* **Template D (VCI Optimization):** Edge collections containing properties that also exist on connected vertices (e.g., `_from_type`, `date`).



### **Phase 2: OWL Ontology Construction (Weeks 3-4)**

**Goal:** Build the `ConceptualModelBuilder` that generates the OWL structure.

1. **Define Mapping Vocabulary (`phys` ontology):**
* Create the Turtle definition for your custom annotations (`phys:mappingStyle`, `phys:edgeCollection`, etc.).


2. **Implement `OWLBuilder` Class:**
* Use `rdflib.js` or `n3`.
* Method `addClass(name, physicalMapping)`: Creates `owl:Class` and adds `phys:` annotations.
* Method `addProperty(name, domain, range, physicalMapping)`: Creates `owl:ObjectProperty` and adds mappings.


3. **Heuristic-to-OWL Translation:**
* Map "Collections detected as Vertex Tables" → `owl:Class` (Mapping: `COLLECTION`).
* Map "Discriminator values in generic tables" → `owl:Class` (Mapping: `LABEL`).
* Map "Edge Collections" → `owl:ObjectProperty` (Mapping: `DEDICATED_COLLECTION`).



### **Phase 3: Agentic Reasoning Pipeline (Weeks 5-6)**

**Goal:** Use LLM to bridge the gap between "Physical Structure" and "Semantic Meaning".

1. **Prompt Engineering:**
* **Input:** Serialized `PhysicalSchemaMetadata` + Sample Documents (from Phase 1).
* **Task:** "Identify the semantic concepts. If a collection is named `c_102` but contains `firstName` and `lastName`, rename the concept to `Person`. Identify if `orders` edges connect `Person` to `Product`."
* **Hybrid Resolution:** "The `users` collection is a standard Document collection, but the `knowledge` collection uses RDF-style edges. Create a unified model where `User` (PG) `creates` (Edge) `Knowledge` (RDF)."


2. **Agent Loop:**
* **Step 1:** Run Heuristics. If `confidence > 0.9` (e.g., perfect GraphRAG match), skip LLM.
* **Step 2:** Generate Prompt with physical stats.
* **Step 3:** LLM generates JSON describing Conceptual-to-Physical map.
* **Step 4:** `OWLBuilder` converts JSON to annotated OWL.



### **Phase 4: Transpiler Integration (Weeks 7-8)**

**Goal:** Connect `arango-cypher` and `arango-sparql` to read the OWL.

1. **Update Transpilers:**
* Modify `arango-cypher` to accept an `owl_schema.ttl` file configuration.
* Replace hardcoded query generation logic with a **Mapping-Driven Query Builder**.
* **Example Logic:**
* *Query:* `MATCH (n:User)`
* *Lookup:* `User` in OWL → `phys:mappingStyle = COLLECTION`, `phys:collectionName = "users_v2"`.
* *Generate:* `FOR n IN users_v2` (PG style).
* *Alternative:* `User` in OWL → `phys:mappingStyle = LABEL`, `phys:collectionName = "nodes"`, `phys:typeVal = "USR"`.
* *Generate:* `FOR n IN nodes FILTER n.type == 'USR'` (LPG style).





### **Phase 5: Optimization & Caching (Week 9)**

**Goal:** Ensure performance via caching as per the PRD.

1. **Fingerprinting:** Generate a hash of the database schema (collection names + indexes).
2. **Cache Storage:** Store the generated `.ttl` file in a system collection `_schema_cache`.
3. **Validation:** If the schema hash changes, trigger a re-run of the Agentic Analyzer.

## **Summary of Key Technologies**

* **Ontology Format:** OWL 2 DL (Turtle serialization).
* **Mapping Format:** RDF Custom Annotations.
* **Detection:** Heuristic Templates (Regex/Stats) + LLM (Semantic Inference).
* **Integration:** NPM Package `@arangodb/schema-analyzer`.

This plan moves you from hardcoded "model detectors" to a flexible, declarative system where the "Schema" is data that the transpilers read, allowing them to support any hybrid architecture the analyzer can describe.