# IMAGE ACCUMULATOR
import gc

class d_que:
    """Simple deque-like container for storing image objects during acquisition.

    This class provides a minimal queue interface for appending, popping,
    and inspecting stored items, with optional discard to help free memory
    when large image objects are no longer needed.
    """

    def __init__(self):
        """Initialize an empty image queue."""
        self.items = []
        
    def is_empty(self):
        """Return True if the queue has no items, otherwise False."""
        return len(self.items) == 0
    
    def append(self, item):
        """Append an item to the rear of the queue."""
        self.items.append(item)

    def popleft(self, discard=False):
        """Remove and return the item from the front of the queue.

        Parameters
        ----------
        discard : bool, optional
            If True, the popped item is deleted and garbage collected and
            the method returns None. If False, the item is returned.

        Returns
        -------
        object or None
            The popped item or None if the queue is empty or discard is True.
        """
        if not self.is_empty():
            item = self.items.pop(0)
            if discard:
                del item
                gc.collect()
                return None
            return item
        return None

    def remove_rear(self, discard=False):
        """Remove and return the item from the rear of the queue.

        Parameters
        ----------
        discard : bool, optional
            If True, the removed item is deleted and garbage collected and
            the method returns None. If False, the item is returned.

        Returns
        -------
        object or None
            The removed item or None if the queue is empty or discard is True.
        """
        if not self.is_empty():
            item = self.items.pop()
            if discard:
                del item
                gc.collect()
                return None
            return item
        return None
                
    def get_item(self, index):
        """Return the `.image` attribute of the item at the given index.

        Parameters
        ----------
        index : int
            Position of the item to access.

        Returns
        -------
        object or None
            The `image` attribute of the item at `index`, or None if out of range.
        """
        if index < len(self.items):
            return self.items[index].image
        else:
            return None       

    def get_xy(self, index):
        """Return the expected X and Y coordinates for the item at the given index.

        Parameters
        ----------
        index : int
            Position of the item to access.

        Returns
        -------
        tuple[float, float] or None
            A tuple `(expected_x, expected_y)` if index is valid, otherwise None.
        """
        if index < len(self.items):
            return self.items[index].expected_x, self.items[index].expected_y

    def size(self):
        """Return the number of items currently stored in the queue."""
        return len(self.items)
