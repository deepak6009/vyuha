
class Visited:
    def __init__(self, x_steps, y_steps):
        """
        x_steps, y_steps: integer grid dimensions
        """
        self.x_steps = int(x_steps)
        self.y_steps = int(y_steps)
        
        # Initialize visited grid: [visited_flag, index, offset_x, offset_y]
        self.visited = [[[0, index, None, None] for index in range(self.x_steps)] for _ in range(self.y_steps)]


    def set_visited(self, index, col, row):
        curr_row = max(0, min(int(row), self.y_steps - 1))
        curr_col = max(0, min(int(col), self.x_steps - 1))
        self.visited[curr_row][curr_col][0] = 1
        self.visited[curr_row][curr_col][1] = index

    def get_index(self, index, col, row):
        curr_row = max(0, min(int(row), self.y_steps - 1))
        curr_col = max(0, min(int(col), self.x_steps - 1))
        positions = [[curr_row, curr_col - 1], [curr_row, curr_col + 1], [curr_row - 1, curr_col], [curr_row + 1, curr_col]]

        for i in positions:
            if 0 <= i[0] < self.y_steps and 0 <= i[1] < self.x_steps:
                if self.visited[i[0]][i[1]][0] == 1:
                    if curr_col - i[1] < 0:
                        return self.visited[i[0]][i[1]][1], True
                    else:
                        return self.visited[i[0]][i[1]][1], False
        return index - 1 if index > 0 else None, False

    def get_offsets(self, col, row):
        curr_row = max(0, min(int(row), self.y_steps - 1))
        curr_col = max(0, min(int(col), self.x_steps - 1))
        return self.visited[curr_row][curr_col][2], self.visited[curr_row][curr_col][3]

    def set_offsets(self, col, row, x_offset, y_offset):
        curr_row = max(0, min(int(row), self.y_steps - 1))
        curr_col = max(0, min(int(col), self.x_steps - 1))
        self.visited[curr_row][curr_col][2] = x_offset
        self.visited[curr_row][curr_col][3] = y_offset
