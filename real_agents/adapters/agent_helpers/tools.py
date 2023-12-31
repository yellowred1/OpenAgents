"""Interface for tools."""
from inspect import signature
from typing import Any, Awaitable, Callable, Dict, Optional, Type, Union
from pydantic import BaseModel, validate_arguments

from langchain.tools.base import BaseTool

from real_agents.adapters.data_model import DataModel
from real_agents.adapters.callbacks.manager import (
    CallbackManager,
    Callbacks,
)


class Tool(BaseTool):
    """Tool that takes in function or coroutine directly."""

    description: str = ""
    func: Callable[..., str]
    """The function to run when the tool is called."""
    coroutine: Optional[Callable[..., Awaitable[str]]] = None
    """The asynchronous version of the function."""

    @property
    def args(self) -> dict:
        if self.args_schema is not None:
            return self.args_schema.schema()["properties"]
        else:
            inferred_model = validate_arguments(self.func).model  # type: ignore
            schema = inferred_model.schema()["properties"]
            valid_keys = signature(self.func).parameters
            return {k: schema[k] for k in valid_keys}

    def _run(self, *args: Any, **kwargs: Any) -> str:
        """Use the tool."""
        return self.func(*args, **kwargs)

    async def _arun(self, *args: Any, **kwargs: Any) -> str:
        """Use the tool asynchronously."""
        if self.coroutine:
            return await self.coroutine(*args, **kwargs)
        raise NotImplementedError("Tool does not support async")

    # TODO: this is for backwards compatibility, remove in future
    def __init__(
        self, name: str, func: Callable[[str], Union[Dict[Any, Any], DataModel]], description: str, **kwargs: Any
    ) -> None:
        """Initialize tool."""
        super(Tool, self).__init__(name=name, func=func, description=description, **kwargs)

    def run(
        self,
        tool_input: Union[str, Dict],
        verbose: Optional[bool] = None,
        start_color: Optional[str] = "green",
        color: Optional[str] = "green",
        callbacks: Callbacks = None,
        **kwargs: Any,
    ) -> Any:
        """Run the tool."""
        parsed_input = self._parse_input(tool_input)
        if not self.verbose and verbose is not None:
            verbose_ = verbose
        else:
            verbose_ = self.verbose

        # todo: fix this place
        callback_manager = CallbackManager.configure(callbacks, self.callbacks, verbose=verbose_)
        # TODO: maybe also pass through run_manager is _run supports kwargs
        new_arg_supported = signature(self._run).parameters.get("run_manager")
        run_manager = callback_manager.on_tool_start(
            {"name": self.name, "description": self.description},
            tool_input if isinstance(tool_input, str) else str(tool_input),
            color=start_color,
            **kwargs,
        )
        try:
            tool_args, tool_kwargs = self._to_args_and_kwargs(parsed_input)
            observation = (
                self._run(*tool_args, run_manager=run_manager, **tool_kwargs)
                if new_arg_supported
                else self._run(*tool_args, **tool_kwargs)
            )
        except (Exception, KeyboardInterrupt) as e:
            run_manager.on_tool_error(e)
            raise e

        run_manager.on_tool_end(observation, color=color, name=self.name, **kwargs)

        return observation


class InvalidTool(BaseTool):
    """Tool that is run when invalid tool name is encountered by agent."""

    name = "invalid_tool"
    description = "Called when tool name is invalid."

    def _run(self, tool_name: str) -> str:
        """Use the tool."""
        return f"{tool_name} is not a valid tool, try another one."

    async def _arun(self, tool_name: str) -> str:
        """Use the tool asynchronously."""
        return f"{tool_name} is not a valid tool, try another one."


def tool(
    *args: Union[str, Callable],
    return_direct: bool = False,
    args_schema: Optional[Type[BaseModel]] = None,
    infer_schema: bool = True,
) -> Callable:
    """Make tools out of functions, can be used with or without arguments.

    Args:
        *args: The arguments to the tool.
        return_direct: Whether to return directly from the tool rather
            than continuing the agent loop.
        args_schema: optional argument schema for user to specify
        infer_schema: Whether to infer the schema of the arguments from
            the function's signature. This also makes the resultant tool
            accept a dictionary input to its `run()` function.

    Requires:
        - Function must be of type (str) -> str
        - Function must have a docstring

    Examples:
        .. code-block:: python

            @tool
            def search_api(query: str) -> str:
                # Searches the API for the query.
                return

            @tool("search", return_direct=True)
            def search_api(query: str) -> str:
                # Searches the API for the query.
                return
    """

    def _make_with_name(tool_name: str) -> Callable:
        def _make_tool(func: Callable) -> Tool:
            assert func.__doc__, "Function must have a docstring"
            # Description example:
            # search_api(query: str) - Searches the API for the query.
            description = f"{tool_name}{signature(func)} - {func.__doc__.strip()}"
            _args_schema = args_schema
            if _args_schema is None and infer_schema:
                _args_schema = validate_arguments(func).model  # type: ignore
            tool_ = Tool(
                name=tool_name,
                func=func,
                args_schema=_args_schema,
                description=description,
                return_direct=return_direct,
            )
            return tool_

        return _make_tool

    if len(args) == 1 and isinstance(args[0], str):
        # if the argument is a string, then we use the string as the tool name
        # Example usage: @tool("search", return_direct=True)
        return _make_with_name(args[0])
    elif len(args) == 1 and callable(args[0]):
        # if the argument is a function, then we use the function name as the tool name
        # Example usage: @tool
        return _make_with_name(args[0].__name__)(args[0])
    elif len(args) == 0:
        # if there are no arguments, then we use the function name as the tool name
        # Example usage: @tool(return_direct=True)
        def _partial(func: Callable[[str], str]) -> BaseTool:
            return _make_with_name(func.__name__)(func)

        return _partial
    else:
        raise ValueError("Too many arguments for tool decorator")
