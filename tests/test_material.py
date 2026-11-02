import unittest

import chess

from chesslab import material


class ImbalanceTest(unittest.TestCase):
    def test_start_is_even(self):
        board = chess.Board()
        self.assertEqual(material.imbalance(board, chess.WHITE), ([], 0))
        self.assertEqual(material.imbalance(board, chess.BLACK), ([], 0))

    def test_pawn_up(self):
        board = chess.Board()
        for san in ("e4", "d5", "exd5"):
            board.push_san(san)
        self.assertEqual(material.imbalance(board, chess.WHITE), ([chess.PAWN], 1))
        self.assertEqual(material.imbalance(board, chess.BLACK), ([], 0))

    def test_exchange_shows_both_sides(self):
        # white has won a rook for a knight: white shows the rook, black the knight, +2 for white
        board = chess.Board("1n2k3/8/8/8/8/8/8/R3K3 w - - 0 1")
        self.assertEqual(material.imbalance(board, chess.WHITE), ([chess.ROOK], 2))
        self.assertEqual(material.imbalance(board, chess.BLACK), ([chess.KNIGHT], 0))


class CapturedTest(unittest.TestCase):
    def test_captured_by_white_grouped(self):
        board = chess.Board("1n2k3/pp3ppp/8/8/8/8/PPP2PPP/R3K3 w - - 0 1")
        # white took from black: q, 2 r, 2 b, 1 n, 3 p -> chess.com order: pawns first, queen last
        self.assertEqual(material.captured(board, chess.WHITE),
                         [chess.PAWN] * 3 + [chess.BISHOP] * 2 + [chess.KNIGHT] + [chess.ROOK] * 2 + [chess.QUEEN])

    def test_promotion_never_negative(self):
        board = chess.Board("4k3/8/8/8/8/8/8/QQ2K3 w - - 0 1")
        self.assertNotIn(chess.QUEEN, material.captured(board, chess.BLACK))
