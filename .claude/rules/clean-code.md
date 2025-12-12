# Clean Code Rules

Based on "Clean Code: A Handbook of Agile Software Craftsmanship" by Robert C. Martin (Uncle Bob)

---

## Philosophy & Mindset

### The Boy Scout Rule
> "Leave the campground cleaner than you found it."

Always leave the code better than you found it. Make small improvements whenever you touch code.

### Core Principles
- **The only way to go fast is to go well** - Short-term shortcuts create long-term slowdowns
- **KISS** - Keep It Simple, Stupid. Simpler is always better. Reduce complexity as much as possible
- **DRY** - Don't Repeat Yourself. Eliminate duplication ruthlessly
- **YAGNI** - You Aren't Gonna Need It. Don't add functionality until it's necessary
- **Principle of Least Surprise** - Code should behave as readers expect

---

## Meaningful Names

### General Rules
- **Reveal intent** - The name should tell you why it exists, what it does, and how it's used
- **Avoid disinformation** - Don't use names that could mislead readers
- **Make meaningful distinctions** - If names must be different, they should mean something different
- **Use pronounceable names** - If you can't pronounce it, you can't discuss it
- **Use searchable names** - Single-letter names and numeric constants are hard to find

### Specific Guidelines
- **Class names** should be nouns or noun phrases (e.g., `Customer`, `WikiPage`, `Account`)
- **Method names** should be verbs or verb phrases (e.g., `postPayment`, `deletePage`, `save`)
- **Pick one word per concept** - Don't use `fetch`, `retrieve`, and `get` for the same operation
- **Avoid encodings** - No Hungarian notation, no type prefixes, no member prefixes
- **Replace magic numbers with named constants** - `SECONDS_PER_DAY = 86400` not just `86400`

---

## Functions

### Size
- Functions should be **small**
- Functions should **do one thing, do it well, and do it only**
- One level of abstraction per function
- The Stepdown Rule: Read code like a narrative, top to bottom

### Arguments
- **Zero arguments (niladic)** - Ideal
- **One argument (monadic)** - Good
- **Two arguments (dyadic)** - Acceptable
- **Three arguments (triadic)** - Avoid when possible
- **More than three** - Requires very special justification

### Rules
- **No side effects** - Don't do hidden things the function name doesn't suggest
- **Command-Query Separation** - Functions should either do something OR answer something, not both
- **Don't use flag arguments** - Split into two functions instead of `render(boolean isSuite)`
- **Don't return null** - Throw an exception or return a special case object
- **Don't pass null** - Unless the API expects it

---

## Comments

### Good Comments (Use Sparingly)
- **Legal comments** - Copyright and license statements
- **Informative comments** - Explaining regular expressions, complex algorithms
- **Explanation of intent** - Why a decision was made
- **Clarification** - When you can't change confusing code you don't own
- **Warning of consequences** - Why something shouldn't be changed
- **TODO comments** - For work that should be done but can't be done now
- **Amplification** - Emphasizing importance of something that seems inconsequential

### Bad Comments (Avoid)
- **Redundant comments** - Restating what the code already clearly says
- **Misleading comments** - Comments that lie or are outdated
- **Mandated comments** - Javadoc for every function is wasteful
- **Journal comments** - Source control handles this
- **Noise comments** - `/** Default constructor */` adds nothing
- **Position markers** - `// ============= Actions ============`
- **Closing brace comments** - `} // end while` indicates the function is too long
- **Commented-out code** - Delete it; source control has the history

### The Best Comment
> "The best comment is a comment you didn't have to write."

Write self-documenting code. If you need a comment, first try to refactor the code to make it clearer.

---

## Formatting

### Vertical Formatting
- **Newspaper metaphor** - Important stuff at the top, details below
- **Vertical openness** - Separate concepts with blank lines
- **Vertical density** - Keep related concepts close together
- **Vertical distance** - Variables should be declared close to their usage
- **Dependent functions** - If one function calls another, keep them vertically close

### Horizontal Formatting
- **Keep lines short** - 80-120 characters maximum
- **Use horizontal whitespace** to associate related things and disassociate weakly related things
- **Don't align variable declarations** - It draws attention to the wrong thing
- **Indentation** - Respect and maintain consistent indentation

### Team Rules
- A team should agree on a single formatting style
- Consistency trumps individual preference

---

## Objects and Data Structures

### The Law of Demeter
A method `f` of class `C` should only call methods of:
- `C` itself
- Objects created by `f`
- Objects passed as arguments to `f`
- Objects held in instance variables of `C`

**Don't talk to strangers.** Avoid chains like `a.getB().getC().doSomething()`

### Data/Object Anti-Symmetry
- **Objects** hide their data behind abstractions and expose functions
- **Data structures** expose their data and have no meaningful functions

Don't create hybrids that are half object and half data structure.

### Data Transfer Objects (DTOs)
- Pure data structures with public variables and no functions
- Useful for communication with databases, parsing messages, etc.

---

## Error Handling

### Use Exceptions Rather Than Return Codes
```
// Bad: Return codes
if (deletePage(page) == E_OK) { ... }

// Good: Exceptions
try {
    deletePage(page);
} catch (Exception e) { ... }
```

### Write Your Try-Catch-Finally First
- Start with `try-catch-finally` when writing code that could throw exceptions
- This helps define what the caller can expect

### Provide Context with Exceptions
- Include enough information to determine the source and location of an error
- Mention the operation that failed and the type of failure

### Don't Return Null
- Returning null creates work for the caller and opportunities for `NullPointerException`
- Consider throwing an exception or returning a special case object (Null Object Pattern)

### Don't Pass Null
- Passing null to methods is worse than returning it
- There's no good way to handle null arguments without validation bloat

---

## Unit Tests

### The Three Laws of TDD
1. **You may not write production code until you have written a failing unit test**
2. **You may not write more of a unit test than is sufficient to fail** (not compiling counts as failing)
3. **You may not write more production code than is sufficient to pass the currently failing test**

### F.I.R.S.T. Principles

- **Fast** - Tests should run quickly so you run them frequently
- **Independent** - Tests should not depend on each other
- **Repeatable** - Tests should work in any environment (dev, staging, prod)
- **Self-Validating** - Tests should have a boolean output: pass or fail
- **Timely** - Tests should be written just before the production code (TDD)

### Clean Tests
- **Readability** - Tests should be as readable as production code
- **One assert per test** - A guideline, not a rule. One concept per test is more important
- **Single concept per test** - Don't test multiple things in one test method
- **Given-When-Then** - Structure tests with setup, action, and verification phases

### Test Code Quality
- Test code is just as important as production code
- Maintain the same quality standards
- Tests enable refactoring and change

---

## Classes

### Class Organization
1. Public static constants
2. Private static variables
3. Private instance variables
4. Public functions
5. Private utilities called by public functions

### Size
- Classes should be **small**
- Measured by **responsibilities**, not lines of code
- You should be able to describe a class in about 25 words without using "if", "and", "or", or "but"

### Single Responsibility Principle (SRP)
- A class should have **one, and only one, reason to change**
- Many small classes > few large classes
- Each class encapsulates a single responsibility

### Cohesion
- Classes should have a small number of instance variables
- Each method should manipulate one or more of those variables
- High cohesion: methods and variables are co-dependent and hang together as a logical whole

### Organizing for Change
- Isolate code that might change from code that won't
- Use interfaces to abstract volatile implementations
- Open-Closed Principle: Open for extension, closed for modification

---

## SOLID Principles

### Single Responsibility Principle (SRP)
> "A class should have one, and only one, reason to change."

- Separate concerns into different classes
- Changes to one responsibility don't affect others
- Makes code easier to understand, test, and maintain

### Open-Closed Principle (OCP)
> "Software entities should be open for extension but closed for modification."

- Add new behavior without changing existing code
- Use abstractions and polymorphism
- Design modules that can be extended without modification

### Liskov Substitution Principle (LSP)
> "Derived classes must be substitutable for their base classes."

- Subtypes must be usable through the base class interface
- Don't strengthen preconditions in derived classes
- Don't weaken postconditions in derived classes

### Interface Segregation Principle (ISP)
> "Clients should not be forced to depend on interfaces they do not use."

- Many small, specific interfaces > one large, general interface
- Don't force implementers to depend on methods they don't need
- Segregate interfaces by client needs

### Dependency Inversion Principle (DIP)
> "Depend on abstractions, not on concretions."

- High-level modules should not depend on low-level modules
- Both should depend on abstractions
- Abstractions should not depend on details

---

## Code Smells

### Bloaters
- **Long Method** - Functions that are too long
- **Large Class** - Classes that are trying to do too much
- **Primitive Obsession** - Overuse of primitives instead of small objects
- **Long Parameter List** - Methods with too many parameters
- **Data Clumps** - Groups of data that appear together repeatedly

### Object-Orientation Abusers
- **Switch Statements** - Often indicate missing polymorphism
- **Refused Bequest** - Subclass doesn't use inherited methods
- **Alternative Classes with Different Interfaces** - Similar classes with different method signatures

### Change Preventers
- **Divergent Change** - One class is changed for many different reasons
- **Shotgun Surgery** - One change requires edits to many different classes
- **Parallel Inheritance Hierarchies** - Creating a subclass requires creating another elsewhere

### Dispensables
- **Comments** - Excessive comments often indicate unclear code
- **Duplicate Code** - Same code in multiple places
- **Dead Code** - Code that is never executed
- **Speculative Generality** - Unused abstractions "just in case"
- **Lazy Class** - Classes that don't do enough to justify their existence

### Couplers
- **Feature Envy** - Method uses another class's data more than its own
- **Inappropriate Intimacy** - Classes that are too tightly coupled
- **Message Chains** - `a.getB().getC().getD().doSomething()`
- **Middle Man** - Class that only delegates to another class

---

## Design Guidelines

### General
- **Prefer polymorphism over conditionals** - Replace switch/if chains with polymorphic dispatch
- **Separate multi-threading code** - Keep concurrency logic separate from other code
- **Avoid over-configurability** - Don't add configuration options until needed
- **Use dependency injection** - Inject dependencies rather than creating them
- **Encapsulate boundary conditions** - Centralize handling of edge cases
- **Use dedicated value objects** - Don't overuse primitives

### Boundaries
- **Keep third-party code at arm's length** - Wrap external APIs
- **Write learning tests** - Test third-party code to learn and verify its behavior
- **Define clear interfaces** for code that doesn't exist yet

### Concurrency
- **Keep synchronized sections small**
- **Avoid shared data where possible**
- **Use thread-safe collections**
- **Know your library's thread safety guarantees**

---

## Summary: The Boy Scout Rule

Every time you touch code:
1. Make it a little better
2. Fix one small thing
3. Leave it cleaner than you found it

Over time, this prevents code rot and improves quality throughout the codebase.

---

*"Clean code always looks like it was written by someone who cares."* — Robert C. Martin
