import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from inference_device import select_device


class DeviceTests(unittest.TestCase):
    @patch('inference_device.torch.version.cuda', None)
    @patch('inference_device.torch.cuda.is_available', return_value=False)
    def test_cpu_build_explains_fallback(self, available):
        device, description = select_device()
        self.assertEqual(device, 'cpu')
        self.assertIn('no CUDA support', description)

    @patch('inference_device.torch.cuda.get_device_name', return_value='Test GPU')
    @patch('inference_device.torch.ones')
    @patch('inference_device.torch.cuda.is_available', return_value=True)
    def test_gpu_selected_and_execution_checked(self, available, ones, name):
        device, description = select_device()
        self.assertEqual(device, 'cuda:0')
        self.assertEqual(str(ones.call_args.kwargs['device']), 'cuda:0')
        self.assertIn('Test GPU', description)
        ones.return_value.__matmul__.assert_called_once()

    @patch('inference_device.torch.ones', side_effect=RuntimeError('unsupported GPU'))
    @patch('inference_device.torch.cuda.is_available', return_value=True)
    def test_failed_gpu_falls_back_with_reason(self, available, ones):
        device, description = select_device()
        self.assertEqual(device, 'cpu')
        self.assertIn('unsupported GPU', description)

    @patch('inference_device.torch.ones', side_effect=RuntimeError('unavailable'))
    def test_explicit_gpu_failure_is_visible(self, ones):
        with self.assertRaisesRegex(RuntimeError, 'requested inference device'):
            select_device('cuda:0')

    def test_explicit_cpu_is_respected(self):
        self.assertEqual(select_device('cpu'), ('cpu', 'CPU (explicitly selected)'))

    @patch('detectors.YOLODetector')
    @patch('inference_device.select_device', return_value=('cuda:0', 'GPU'))
    def test_factory_passes_resolved_device(self, select, detector):
        from detectors import load_detector
        load_detector(Path('model.pt'), None)
        detector.assert_called_once_with(Path('model.pt'), device='cuda:0')

    @patch('detectors.RFDETRDetector')
    @patch('inference_device.select_device', return_value=('cuda:0', 'GPU'))
    def test_rfdetr_factory_passes_resolved_device(self, select, detector):
        from detectors import load_detector
        load_detector(Path('model.pth'), None)
        detector.assert_called_once_with(Path('model.pth'), device='cuda:0')

    @patch('worker.run_pipeline', return_value={})
    @patch('worker.select_device', return_value=('cuda:0', 'CUDA — Test GPU'))
    def test_worker_reports_device_it_passes_to_pipeline(self, select, run):
        from pipeline import RunConfig
        from worker import PipelineWorker
        config = RunConfig(Path('video.mp4'), Path('model.pt'), Path('output'))
        worker = PipelineWorker(config)
        descriptions = []
        worker.device.connect(descriptions.append)
        worker.run()
        self.assertEqual(run.call_args.kwargs['config'].device, 'cuda:0')
        self.assertEqual(descriptions, ['CUDA — Test GPU'])


if __name__ == '__main__':
    unittest.main()
