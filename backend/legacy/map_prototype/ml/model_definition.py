import torch
import torch.nn as nn


class ConvLSTMCell(nn.Module):
    def __init__(self, in_channels, hidden_dim, kernel_size, bias):
        super(ConvLSTMCell, self).__init__()
        self.in_channels = in_channels
        self.hidden_dim = hidden_dim
        self.kernel_size = kernel_size
        self.padding = kernel_size[0] // 2, kernel_size[1] // 2
        self.bias = bias

        self.conv = nn.Conv2d(
            in_channels + hidden_dim,
            4 * hidden_dim,
            kernel_size,
            padding=self.padding,
            bias=self.bias
        )

    def forward(self, input_tensor, cur_state):
        h_cur, c_cur = cur_state

        combined_conv_input = torch.cat([input_tensor, h_cur], dim=1)
        combined_conv_output = self.conv(combined_conv_input)

        cc_i, cc_f, cc_o, cc_g = torch.split(
            combined_conv_output,
            self.hidden_dim,
            dim=1
        )

        i = torch.sigmoid(cc_i)
        f = torch.sigmoid(cc_f)
        o = torch.sigmoid(cc_o)
        g = torch.tanh(cc_g)

        c_next = f * c_cur + i * g
        h_next = o * torch.tanh(c_next)

        return h_next, c_next

    def init_hidden(self, batch_size, image_size):
        height, width = image_size
        return (
            torch.zeros(batch_size, self.hidden_dim, height, width, device=self.conv.weight.device),
            torch.zeros(batch_size, self.hidden_dim, height, width, device=self.conv.weight.device)
        )


class ConvLSTM(nn.Module):
    def __init__(
        self,
        in_channels,
        hidden_dim,
        kernel_size,
        num_layers,
        batch_first=True,
        bias=True,
        return_all_layers=False
    ):
        super(ConvLSTM, self).__init__()

        kernel_size = self._extend_for_multilayer(kernel_size, num_layers)
        hidden_dim = self._extend_for_multilayer(hidden_dim, num_layers)

        if not len(kernel_size) == len(hidden_dim) == num_layers:
            raise ValueError("`kernel_size` and `hidden_dim` must have the same length as `num_layers`")

        self.in_channels = in_channels
        self.hidden_dim = hidden_dim
        self.kernel_size = kernel_size
        self.num_layers = num_layers
        self.batch_first = batch_first
        self.bias = bias
        self.return_all_layers = return_all_layers

        cell_list = []
        for i in range(self.num_layers):
            cur_input_dim = self.in_channels if i == 0 else self.hidden_dim[i - 1]

            cell_list.append(
                ConvLSTMCell(
                    in_channels=cur_input_dim,
                    hidden_dim=self.hidden_dim[i],
                    kernel_size=self.kernel_size[i],
                    bias=self.bias
                )
            )

        self.cell_list = nn.ModuleList(cell_list)

        self.output_conv = nn.Conv2d(
            self.hidden_dim[-1],
            self.in_channels,
            kernel_size=(1, 1),
            padding=0
        )

    def forward(self, input_tensor, hidden_state=None):
        if not self.batch_first:
            input_tensor = input_tensor.permute(1, 0, 2, 3, 4)

        b, seq_len, _, h, w = input_tensor.size()

        if hidden_state is not None:
            raise NotImplementedError("Custom hidden state initialization is not implemented.")
        else:
            hidden_state = []
            for i in range(self.num_layers):
                hidden_state.append(self.cell_list[i].init_hidden(b, (h, w)))

        cur_layer_input = input_tensor

        for layer_idx in range(self.num_layers):
            h_state, c_state = hidden_state[layer_idx]
            output_inner = []

            for t in range(seq_len):
                h_state, c_state = self.cell_list[layer_idx](
                    cur_layer_input[:, t, :, :, :],
                    [h_state, c_state]
                )
                output_inner.append(h_state)

            cur_layer_input = torch.stack(output_inner, dim=1)

        final_prediction = self.output_conv(h_state)
        return final_prediction

    def _extend_for_multilayer(self, param, num_layers):
        if not isinstance(param, list):
            param = [param] * num_layers
        return param