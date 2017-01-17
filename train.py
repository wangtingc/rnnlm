# -*- coding: utf-8 -*-

# Script for training an RNNLM model (Elman RNN, LSTM, GRU) on a given corpus.
# Tokens in the corpus must be splitted with spaces before running this script.

import argparse
import math
import os
import sys

import chainer
from chainer import cuda, serializers, optimizers
import chainer.functions as F
import numpy as np
import pyprind

import config
import models
import utils


xp = cuda.cupy
# xp = np


def evaluate(model, sents, ivocab):
    train = False
    n = len(sents)
    loss = 0.0
    acc = 0.0
    vocab_size = model.vocab_size
    for data_i in pyprind.prog_bar(xrange(n)):
        words = sents[data_i:data_i+1]
        xs = utils.make_batch(words, train=train, tail=False)

        ys = model.forward(ts=xs, train=train)

        ys = F.concat(ys, axis=0)
        ys = F.reshape(ys, (-1, vocab_size))
        ts = F.concat(xs, axis=0)
        ts = F.reshape(ts, (-1,))

        loss += F.softmax_cross_entropy(ys, ts)
        acc += F.accuracy(ys, ts, ignore_label=-1)

    loss_data = float(cuda.to_cpu(loss.data)) / n
    acc_data = float(cuda.to_cpu(acc.data)) / n


    for data_i in np.random.randint(0, len(sents), 5):
        words = sents[data_i:data_i+1]
        xs = utils.make_batch(words, train=train, tail=False)

        ys = model.forward(x_init=xs[0], train=train)

        print "Reference:"
        words_ref = [ivocab[w] for w in words[0]]
        print " ".join(words_ref)

        print "Generated:"
        words_gen = [ivocab[w[0]] for w in ys]
        print " ".join(words_gen)

    return loss_data, acc_data


def main(gpu,
        experiment,
        path_corpus,
        path_corpus_val,
        path_corpus_test,
        do_preprocess,
        path_word2vec):

    do_preprocess = True if do_preprocess > 0 else False

    max_epoch = 50
    EVAL = 5000

    model_name = config.hyperparams[experiment]["model"]
    word_dim = config.hyperparams[experiment]["word_dim"]
    state_dim = config.hyperparams[experiment]["state_dim"]
    grad_clip = config.hyperparams[experiment]["grad_clip"]
    weight_decay = config.hyperparams[experiment]["weight_decay"]
    batch_size = config.hyperparams[experiment]["batch_size"]
    
    print "CORPUS: %s" % path_corpus
    if not None in [path_corpus_val, path_corpus_test]:
        print "CORPUS (validation): %s" % path_corpus_val
        print "CORPUS (test): %s" % path_corpus_test
    print "PREPROCESS: %s" % do_preprocess
    print "PRE-TRAINED WORD EMBEDDINGS: %s" % path_word2vec
    print "EXPERIMENT: %s" % experiment
    print "WORD DIM: %d" % word_dim
    print "STATE DIM: %d" % state_dim
    print "GRADIENT CLIPPING: %f" % grad_clip
    print "WEIGHT DECAY: %f" % weight_decay
    print "BATCH SIZE: %d" % batch_size

    path_save_head = os.path.join(config.path_snapshot,
            "rnnlm.%s.%s" % (os.path.basename(path_corpus), experiment))
    print "SNAPSHOT: %s" % path_save_head
    
    sents_train, sents_val, sents_test, vocab, ivocab = \
            utils.load_corpus(
                    path=path_corpus,
                    path_val=path_corpus_val,
                    path_test=path_corpus_test,
                    preprocess=do_preprocess)

    if path_word2vec is not None:
        word2vec = utils.load_word2vec(path_word2vec, word_dim)
        word_embeddings = utils.create_word_embeddings(vocab, word2vec, dim=word_dim, scale=0.001)
    else:
        word_embeddings = None

    if model_name == "rnn":
        model = models.RNN(
                vocab_size=len(vocab),
                word_dim=word_dim,
                state_dim=state_dim,
                word_embeddings=word_embeddings,
                EOS_ID=vocab["<EOS>"])
    elif model_name == "lstm":
        model = models.LSTM(
                vocab_size=len(vocab),
                word_dim=word_dim,
                state_dim=state_dim,
                word_embeddings=word_embeddings,
                EOS_ID=vocab["<EOS>"])
    elif model_name == "gru":
        model = models.GRU(
                vocab_size=len(vocab),
                word_dim=word_dim,
                state_dim=state_dim,
                word_embeddings=word_embeddings,
                EOS_ID=vocab["<EOS>"])
    else:
        print "Unknown model name: %s" % model_name
        sys.exit(-1)
    if gpu >= 0:
        cuda.get_device(gpu).use()
        model.to_gpu(gpu)

    opt = optimizers.SMORMS3()
    opt.setup(model)
    opt.add_hook(chainer.optimizer.GradientClipping(grad_clip))
    opt.add_hook(chainer.optimizer.WeightDecay(weight_decay))
    
    it = 0
    n_train = len(sents_train)
    vocab_size = model.vocab_size
    for epoch in xrange(1, max_epoch+1):
        perm = np.random.permutation(n_train)
        for data_i in xrange(0, n_train, batch_size):
            if data_i + batch_size > n_train:
                break
            words = sents_train[perm[data_i:data_i+batch_size]]
            xs = utils.make_batch(words, train=True, tail=False)

            ys = model.forward(ts=xs, train=True)

            ys = F.concat(ys, axis=0)
            ys = F.reshape(ys, (-1, vocab_size)) # (TN, |V|)
            ts = F.concat(xs, axis=0)
            ts = F.reshape(ts, (-1,)) # (TN,)

            loss = F.softmax_cross_entropy(ys, ts)
            acc = F.accuracy(ys, ts, ignore_label=-1)

            model.zerograds()
            loss.backward()
            loss.unchain_backward()
            opt.update()
            it += 1

            NT = ts.data.shape[0]
            loss_data = float(cuda.to_cpu(loss.data))
            perp = math.exp(loss_data)
            acc_data = float(cuda.to_cpu(acc.data))
            print "[training] epoch=%d (%d/%d=%.03f%%), iter=%d, perplexity=%f, accuracy=%.2f%%, # of tokens=%d" \
                    % (epoch, data_i+batch_size, n_train,
                        float(data_i+batch_size)/n_train*100,
                        it, perp, acc_data*100, NT)

            if it % EVAL == 0:
                print "Evaluating on the validation sentences ..."
                loss_data, acc_data = evaluate(model, sents_val, ivocab)
                perp = math.exp(loss_data)
                print "[validation] epoch=%d, perplexity=%f, accuracy=%.2f%%" \
                        % (epoch, perp, acc_data*100)

                print "Evaluating on the test sentences ..."
                loss_data, acc_data = evaluate(model, sents_test, ivocab)
                perp = math.exp(loss_data)
                print "[test] epoch=%d, perplexity=%f, accuracy=%.2f%%" \
                        % (epoch, perp, acc_data*100)

                serializers.save_npz(path_save_head + ".iter_%d_epoch_%d.model" % (it, epoch), model)
                utils.save_word2vec(path_save_head + ".iter_%d_epoch_%d.vectors.txt" % (it, epoch), utils.extract_word2vec(model, vocab))
                print "Saved."

    print "Done."


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-g", "--gpu", help="gpu", default=0, type=int)
    parser.add_argument("-e", "--experiment", help="experiment", type=str)
    parser.add_argument("-c", "--corpus", help="path to corpus", type=str)
    parser.add_argument("-cv", "--corpus_val", help="path to validation corpus", type=str)
    parser.add_argument("-ct", "--corpus_test", help="path to test corpus", type=str)
    parser.add_argument("-p", "--preprocess", help="preprocessing", type=int)
    parser.add_argument("-w", "--word2vec", help="path to pre-trained word vectors", type=str, default=None)
    args = parser.parse_args()

    gpu = args.gpu
    experiment = args.experiment
    path_corpus = args.corpus
    path_corpus_val = args.corpus_val
    path_corpus_test = args.corpus_test
    preprocess = args.preprocess
    path_word2vec = args.word2vec

    main(gpu=gpu,
        experiment=experiment,
        path_corpus=path_corpus,
        path_corpus_val=path_corpus_val,
        path_corpus_test=path_corpus_test,
        do_preprocess=preprocess,
        path_word2vec=path_word2vec)
