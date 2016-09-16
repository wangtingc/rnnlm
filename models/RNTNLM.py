# -*- coding: utf-8 -*-


import chainer
from chainer import cuda, Variable
import chainer.functions as F
import chainer.links as L
import numpy as np


class RNTNLM(chainer.Chain):
    
    def __init__(self, vocab_size, state_dim):
        self.vocab_size = vocab_size
        self.state_dim = state_dim
        super(RNTNLM, self).__init__(
                embed=L.EmbedID(vocab_size, state_dim),
                bilinear_1=L.Bilinear(state_dim, state_dim, 4 * state_dim),
                linear_2=L.Linear(state_dim, vocab_size),
                )

    
    def forward(self, x, state, train):
        v = self.embed(x)
        
        h_in = self.bilinear_1(F.dropout(v, train=train), state["h"])
        c, h = F.lstm(state["c"], h_in)
        
        y = self.linear_2(F.dropout(h, train=train))

        state = {"h": h, "c": c}
        return y, state


    def initialize_state(self, batch_size, train):
        state = {}
        for key in ["h", "c"]:
            state[key] = Variable(cuda.cupy.zeros((batch_size, self.state_dim), dtype=np.float32), volatile=not train)
        return state
