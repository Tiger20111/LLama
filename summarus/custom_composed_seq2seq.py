from typing import Dict, Optional

import torch
from overrides import overrides

from allennlp.common.checks import ConfigurationError
from allennlp.data.vocabulary import Vocabulary
from allennlp.models.model import Model
from allennlp.modules import TextFieldEmbedder, Seq2SeqEncoder, Embedding
from allennlp.modules.text_field_embedders import BasicTextFieldEmbedder
from allennlp.modules.seq2seq_decoders.seq_decoder import SeqDecoder
from allennlp.nn import util, InitializerApplicator, RegularizerApplicator


@Model.register("custom_composed_seq2seq")
class CustomComposedSeq2Seq(Model):
    def __init__(self,
                 vocab: Vocabulary,
                 source_text_embedder: TextFieldEmbedder,
                 encoder: Seq2SeqEncoder,
                 decoder: SeqDecoder,
                 tied_source_embedder_key: Optional[str] = None,
                 initializer: InitializerApplicator = InitializerApplicator(),
                 regularizer: Optional[RegularizerApplicator] = None) -> None:

        super(CustomComposedSeq2Seq, self).__init__(vocab, regularizer)

        self._source_text_embedder = source_text_embedder
        self._encoder = encoder
        self._decoder = decoder

        if self._encoder.get_output_dim() != self._decoder.get_output_dim():
            raise ConfigurationError(f"Encoder output dimension {self._encoder.get_output_dim()} should be"
                                     f" equal to decoder dimension {self._decoder.get_output_dim()}.")
        if tied_source_embedder_key:
            # A bit of a ugly hack to tie embeddings.
            # Works only for `BasicTextFieldEmbedder`, and since
            # it can have multiple embedders, and `SeqDecoder` contains only a single embedder, we need
            # the key to select the source embedder to replace it with the target embedder from the decoder.
            if not isinstance(self._source_text_embedder, BasicTextFieldEmbedder):
                raise ConfigurationError("Unable to tie embeddings,"
                                         "Source text embedder is not an instance of `BasicTextFieldEmbedder`.")
            # pylint: disable=protected-access
            source_embedder = self._source_text_embedder._token_embedders[tied_source_embedder_key]
            if not isinstance(source_embedder, Embedding):
                raise ConfigurationError("Unable to tie embeddings,"
                                         "Selected source embedder is not an instance of `Embedding`.")
            if source_embedder.get_output_dim() != self._decoder.target_embedder.get_output_dim():
                raise ConfigurationError(f"Output Dimensions mismatch between"
                                         f"source embedder and target embedder.")
            self._source_text_embedder._token_embedders[tied_source_embedder_key] = self._decoder.target_embedder
        initializer(self)

    @overrides
    def forward(self,  # type: ignore
                source_tokens: Dict[str, torch.LongTensor],
                target_tokens: Dict[str, torch.LongTensor] = None) -> Dict[str, torch.Tensor]:
        # pylint: disable=arguments-differ
        """
        Make foward pass on the encoder and decoder for producing the entire target sequence.
        Parameters
        ----------
        source_tokens : ``Dict[str, torch.LongTensor]``
           The output of `TextField.as_array()` applied on the source `TextField`. This will be
           passed through a `TextFieldEmbedder` and then through an encoder.
        target_tokens : ``Dict[str, torch.LongTensor]``, optional (default = None)
           Output of `Textfield.as_array()` applied on target `TextField`. We assume that the
           target tokens are also represented as a `TextField`.
        Returns
        -------
        Dict[str, torch.Tensor]
            The output tensors from the decoder.
        """
        state = self._encode(source_tokens)

        return self._decoder(state, target_tokens)

    @overrides
    def decode(self, output_dict: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """
        Finalize predictions.
        """
        return self._decoder.post_process(output_dict)

    def _encode(self, source_tokens: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """
        Make foward pass on the encoder.
        Parameters
        ----------
        source_tokens : ``Dict[str, torch.LongTensor]``
           The output of `TextField.as_array()` applied on the source `TextField`. This will be
           passed through a `TextFieldEmbedder` and then through an encoder.
        Returns
        -------
        Dict[str, torch.Tensor]
            Map consisting of the key `source_mask` with the mask over the
            `source_tokens` text field,
            and the key `encoder_outputs` with the output tensor from
            forward pass on the encoder.
        """
        # shape: (batch_size, max_input_sequence_length, encoder_input_dim)
        embedded_input = self._source_text_embedder(source_tokens)
        # shape: (batch_size, max_input_sequence_length)
        source_mask = util.get_text_field_mask(source_tokens)
        # shape: (batch_size, max_input_sequence_length, encoder_output_dim)
        encoder_outputs = self._encoder(embedded_input, source_mask.bool())
        return {
            "source_mask": source_mask,
            "encoder_outputs": encoder_outputs,
        }

    @overrides
    def get_metrics(self, reset: bool = False) -> Dict[str, float]:
        return self._decoder.get_metrics(reset)