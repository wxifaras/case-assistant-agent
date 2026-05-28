using System;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;
using System.Threading.Tasks;
using Azure.Core;
using Azure.Identity;
using Azure.Storage.Blobs;
using Azure.Storage.Blobs.Models;
using Microsoft.Identity.Client;
using Microsoft.InformationProtection;
using Microsoft.InformationProtection.Exceptions;
using Microsoft.InformationProtection.File;

namespace MipRelabelTest;

internal static class Program
{
	private static int Main(string[] args)
	{
		try
		{
			var options = CliOptions.Parse(args);
			Run(options).GetAwaiter().GetResult();
			return 0;
		}
		catch (Exception ex)
		{
			Console.Error.WriteLine("ERROR: " + ex.Message);
			return 1;
		}
	}

	private static async Task Run(CliOptions options)
	{
		Console.WriteLine("Starting MIP blob relabel test");
		Console.WriteLine($"- blob: {options.BlobName}");
		if (!string.IsNullOrWhiteSpace(options.TargetLabelId))
		{
			Console.WriteLine($"- target_label_id: {options.TargetLabelId}");
		}
		if (!string.IsNullOrWhiteSpace(options.TargetLabelName))
		{
			Console.WriteLine($"- target_label_name: {options.TargetLabelName}");
		}
		if (options.VerifyOnly)
		{
			Console.WriteLine("- verify_only: true");
		}

		var blobCredential = new DefaultAzureCredential();
		var blobClient = new BlobClient(new Uri($"{options.AccountUrl.TrimEnd('/')}/{options.Container}/{options.BlobName}"), blobCredential);

		var blobProps = await blobClient.GetPropertiesAsync().ConfigureAwait(false);
		var suffix = Path.GetExtension(options.BlobName);
		if (string.IsNullOrWhiteSpace(suffix))
		{
			suffix = ".bin";
		}

		var workDir = Path.Combine(Path.GetTempPath(), "mip-relabel-" + Guid.NewGuid().ToString("N"));
		Directory.CreateDirectory(workDir);

		var inputPath = Path.Combine(workDir, "input" + suffix);
		var outputPath = Path.Combine(workDir, "output" + suffix);

		try
		{
			await DownloadBlobAsync(blobClient, inputPath).ConfigureAwait(false);
			File.Copy(inputPath, outputPath, true);

			var inputHash = Sha256(inputPath);
			Console.WriteLine($"- input_sha256: {inputHash}");

			var appInfo = new ApplicationInfo
			{
				ApplicationId = options.MipAppId,
				ApplicationName = "MipRelabelTest",
				ApplicationVersion = "1.0.0",
			};

			var authDelegate = new AuthDelegate(options, appInfo);
			var identityValue = options.MipUserUpn;
			if (string.IsNullOrWhiteSpace(identityValue))
			{
				identityValue = $"{options.MipAppId}@{options.MipTenantId}";
			}
			var identity = new Identity(identityValue);

			MIP.Initialize(MipComponent.File);

			using var mipContext = MIP.CreateMipContext(new MipConfiguration(
				appInfo,
				"mip_data",
				Microsoft.InformationProtection.LogLevel.Trace,
				false,
				CacheStorageType.OnDiskEncrypted));
			var profileSettings = new FileProfileSettings(mipContext, CacheStorageType.OnDiskEncrypted, new ConsentDelegate());
			using var profile = await MIP.LoadFileProfileAsync(profileSettings).ConfigureAwait(false);

			var engineSettings = new FileEngineSettings(string.Empty, authDelegate, string.Empty, "en-US")
			{
				Identity = identity,
			};

			using var engine = await profile.AddEngineAsync(engineSettings).ConfigureAwait(false);

			if (options.ListLabels)
			{
				Console.WriteLine("Available labels:");
				foreach (var label in engine.SensitivityLabels)
				{
					Console.WriteLine($"- {label.Name}: {label.Id}");
				}
				return;
			}

			using var handler = await engine.CreateFileHandlerAsync(inputPath, inputPath, true).ConfigureAwait(false);
			if (handler.Label != null)
			{
				Console.WriteLine($"- current_label: {handler.Label.Label.Name} ({handler.Label.Label.Id})");
				Console.WriteLine($"- is_protected: {handler.Label.IsProtectionAppliedFromLabel}");
			}
			else
			{
				Console.WriteLine("- current_label: <none>");
			}

			if (options.VerifyOnly)
			{
				Console.WriteLine("Verification complete. No relabel or blob overwrite performed.");
				return;
			}

			var targetLabel = !string.IsNullOrWhiteSpace(options.TargetLabelId)
				? engine.GetLabelById(options.TargetLabelId)
				: engine.SensitivityLabels.FirstOrDefault(l => string.Equals(l.Name, options.TargetLabelName, StringComparison.OrdinalIgnoreCase));
			if (targetLabel == null)
			{
				var knownLabels = string.Join(", ", engine.SensitivityLabels.Select(l => $"{l.Name}:{l.Id}"));
				throw new InvalidOperationException("Target label not found in engine labels. Available labels: " + knownLabels);
			}

			var labelingOptions = new LabelingOptions
			{
				AssignmentMethod = AssignmentMethod.Standard,
			};

			try
			{
				handler.SetLabel(targetLabel, labelingOptions, new ProtectionSettings());
			}
			catch (JustificationRequiredException)
			{
				if (string.IsNullOrWhiteSpace(options.Justification))
				{
					throw new InvalidOperationException(
						"Downgrade justification is required by policy. Re-run with --justification '<reason>'.");
				}

				labelingOptions.IsDowngradeJustified = true;
				labelingOptions.JustificationMessage = options.Justification;
				handler.SetLabel(targetLabel, labelingOptions, new ProtectionSettings());
			}

			if (!handler.IsModified())
			{
				Console.WriteLine("- handler_is_modified: false (no file changes to commit)");
			}
			else
			{
				await handler.CommitAsync(outputPath).ConfigureAwait(false);
			}

			var outputHash = Sha256(outputPath);
			Console.WriteLine($"- output_sha256: {outputHash}");
			Console.WriteLine($"- content_changed: {!string.Equals(inputHash, outputHash, StringComparison.OrdinalIgnoreCase)}");

			await UploadBlobAsync(blobClient, outputPath, blobProps.Value).ConfigureAwait(false);
			Console.WriteLine("Blob overwrite complete.");

			if (options.RunIndexer)
			{
				await TriggerIndexerAsync(options.ApiBaseUrl).ConfigureAwait(false);
			}
		}
		finally
		{
			if (!options.KeepTemp && Directory.Exists(workDir))
			{
				Directory.Delete(workDir, recursive: true);
			}
			else
			{
				Console.WriteLine("Kept temp directory: " + workDir);
			}
		}
	}

	private static async Task DownloadBlobAsync(BlobClient client, string path)
	{
		using var file = File.Open(path, FileMode.Create, FileAccess.Write, FileShare.None);
		var download = await client.DownloadStreamingAsync().ConfigureAwait(false);
		await download.Value.Content.CopyToAsync(file).ConfigureAwait(false);
	}

	private static async Task UploadBlobAsync(BlobClient client, string path, BlobProperties sourceProps)
	{
		var contentSettings = new BlobHttpHeaders
		{
			ContentType = sourceProps.ContentType,
			ContentLanguage = sourceProps.ContentLanguage,
			ContentEncoding = sourceProps.ContentEncoding,
			ContentDisposition = sourceProps.ContentDisposition,
			CacheControl = sourceProps.CacheControl,
		};

		using var data = File.OpenRead(path);
		await client.UploadAsync(data, new BlobUploadOptions
		{
			HttpHeaders = contentSettings,
			Metadata = sourceProps.Metadata,
		}).ConfigureAwait(false);
	}

	private static async Task TriggerIndexerAsync(string apiBaseUrl)
	{
		using var http = new HttpClient();
		var run = new StringContent("{\"reset\":false}", Encoding.UTF8, "application/json");
		var runResponse = await http.PostAsync(apiBaseUrl.TrimEnd('/') + "/api/pipeline/run-indexer", run).ConfigureAwait(false);
		runResponse.EnsureSuccessStatusCode();
		Console.WriteLine("Indexer run triggered.");
	}

	private static string Sha256(string path)
	{
		using var file = File.OpenRead(path);
		using var sha = SHA256.Create();
		var hash = sha.ComputeHash(file);
		return BitConverter.ToString(hash).Replace("-", string.Empty).ToLowerInvariant();
	}
}

internal sealed class ConsentDelegate : IConsentDelegate
{
	public Consent GetUserConsent(string url) => Consent.Accept;
}

internal sealed class AuthDelegate : IAuthDelegate
{
	private readonly CliOptions _options;
	private readonly ApplicationInfo _appInfo;

	public AuthDelegate(CliOptions options, ApplicationInfo appInfo)
	{
		_options = options;
		_appInfo = appInfo;
	}

	public string AcquireToken(Identity identity, string authority, string resource, string claim)
	{
		if (_options.UseDefaultAzureCredential)
		{
			var scope = resource.EndsWith("/", StringComparison.Ordinal) ? resource + ".default" : resource + "/.default";
			var credentialOptions = new DefaultAzureCredentialOptions();
			if (!string.IsNullOrWhiteSpace(_options.ManagedIdentityClientId))
			{
				credentialOptions.ManagedIdentityClientId = _options.ManagedIdentityClientId;
			}

			var dac = new DefaultAzureCredential(credentialOptions);
			var accessToken = dac.GetToken(new TokenRequestContext(new[] { scope }), default);
			return accessToken.Token;
		}

		if (string.IsNullOrWhiteSpace(authority))
		{
			authority = "https://login.microsoftonline.com/" + _options.MipTenantId;
		}
		else
		{
			var authorityUri = new Uri(authority);
			authority = "https://" + authorityUri.Host + "/" + _options.MipTenantId;
		}

		var scopes = new[] { resource.EndsWith("/", StringComparison.Ordinal) ? resource + ".default" : resource + "/.default" };

		if (!_options.UseCertificateAuth && string.IsNullOrWhiteSpace(_options.MipClientSecret))
		{
			var publicClient = PublicClientApplicationBuilder
				.Create(_appInfo.ApplicationId)
				.WithAuthority(authority)
				.WithDefaultRedirectUri()
				.Build();

			var accounts = publicClient.GetAccountsAsync().GetAwaiter().GetResult();
			AuthenticationResult delegatedToken;
			try
			{
				delegatedToken = publicClient
					.AcquireTokenSilent(scopes, accounts.FirstOrDefault())
					.ExecuteAsync()
					.GetAwaiter()
					.GetResult();
			}
			catch (MsalUiRequiredException)
			{
				delegatedToken = publicClient
					.AcquireTokenInteractive(scopes)
					.WithPrompt(Prompt.SelectAccount)
					.ExecuteAsync()
					.GetAwaiter()
					.GetResult();
			}

			return delegatedToken.AccessToken;
		}

		IConfidentialClientApplication app;
		if (_options.UseCertificateAuth)
		{
			if (string.IsNullOrWhiteSpace(_options.CertThumbprint))
			{
				throw new InvalidOperationException("MIP cert auth enabled but MIP_CERT_THUMBPRINT is missing.");
			}

			var cert = ReadCertificateFromStore(_options.CertThumbprint);
			if (cert == null)
			{
				throw new InvalidOperationException("Certificate not found in CurrentUser\\My: " + _options.CertThumbprint);
			}

			app = ConfidentialClientApplicationBuilder
				.Create(_appInfo.ApplicationId)
				.WithCertificate(cert)
				.Build();
		}
		else
		{
			if (string.IsNullOrWhiteSpace(_options.MipClientSecret))
			{
				throw new InvalidOperationException("MIP client secret auth selected but MIP_CLIENT_SECRET is missing.");
			}

			app = ConfidentialClientApplicationBuilder
				.Create(_appInfo.ApplicationId)
				.WithClientSecret(_options.MipClientSecret)
				.Build();
		}

		var token = app
			.AcquireTokenForClient(scopes)
			.WithTenantId(_options.MipTenantId)
			.ExecuteAsync()
			.GetAwaiter()
			.GetResult();

		return token.AccessToken;
	}

	private static X509Certificate2 ReadCertificateFromStore(string thumbprint)
	{
		using var store = new X509Store(StoreName.My, StoreLocation.CurrentUser);
		store.Open(OpenFlags.ReadOnly);
		var certs = store.Certificates.Find(X509FindType.FindByThumbprint, thumbprint, false);
		return certs.Count > 0 ? certs[0] : null;
	}
}

internal sealed class CliOptions
{
	public string AccountUrl { get; private set; }
	public string Container { get; private set; }
	public string BlobName { get; private set; }
	public string TargetLabelId { get; private set; }
	public string TargetLabelName { get; private set; }
	public string Justification { get; private set; }
	public bool RunIndexer { get; private set; }
	public bool KeepTemp { get; private set; }
	public bool ListLabels { get; private set; }
	public bool VerifyOnly { get; private set; }
	public string ApiBaseUrl { get; private set; }
	public string MipAppId { get; private set; }
	public string MipTenantId { get; private set; }
	public string MipClientSecret { get; private set; }
	public string MipUserUpn { get; private set; }
	public bool UseCertificateAuth { get; private set; }
	public string CertThumbprint { get; private set; }
	public bool UseDefaultAzureCredential { get; private set; }
	public string ManagedIdentityClientId { get; private set; }

	public static CliOptions Parse(string[] args)
	{
		string Arg(string name, string envName = null, bool required = false, string defaultValue = null)
		{
			var key = "--" + name;
			var idx = Array.IndexOf(args, key);
			string value = null;
			if (idx >= 0 && idx < args.Length - 1)
			{
				value = args[idx + 1];
			}
			if (string.IsNullOrWhiteSpace(value) && !string.IsNullOrWhiteSpace(envName))
			{
				value = Environment.GetEnvironmentVariable(envName);
			}
			if (string.IsNullOrWhiteSpace(value))
			{
				value = defaultValue;
			}
			if (required && string.IsNullOrWhiteSpace(value))
			{
				throw new ArgumentException($"Missing required option --{name} or env {envName}.");
			}
			return value;
		}

		bool Flag(string name)
		{
			return args.Any(a => string.Equals(a, "--" + name, StringComparison.OrdinalIgnoreCase));
		}

		var options = new CliOptions
		{
			AccountUrl = Arg("account-url", "BLOBSTORAGE_ACCOUNT_URL", required: true),
			Container = Arg("container", "BLOBSTORAGE_CONTAINER_NAME", required: true),
			BlobName = Arg("blob-name", required: true),
			TargetLabelId = Arg("target-label-id", "MIP_TARGET_LABEL_ID", required: false),
			TargetLabelName = Arg("target-label-name", "MIP_TARGET_LABEL_NAME", required: false),
			Justification = Arg("justification", "MIP_JUSTIFICATION", required: false),
			RunIndexer = Flag("run-indexer"),
			KeepTemp = Flag("keep-temp"),
			ListLabels = Flag("list-labels"),
			VerifyOnly = Flag("verify-only"),
			ApiBaseUrl = Arg("api-base-url", defaultValue: "http://localhost:8000"),
			MipAppId = Arg("mip-app-id", "MIP_APP_ID", required: true),
			MipTenantId = Arg("mip-tenant-id", "MIP_TENANT_ID", required: true),
			MipClientSecret = Arg("mip-client-secret", "MIP_CLIENT_SECRET", required: false),
			MipUserUpn = Arg("mip-user-upn", "MIP_USER_UPN", required: false),
			CertThumbprint = Arg("mip-cert-thumbprint", "MIP_CERT_THUMBPRINT", required: false),
			UseCertificateAuth = Flag("use-cert-auth") || string.Equals(Environment.GetEnvironmentVariable("MIP_USE_CERT_AUTH"), "true", StringComparison.OrdinalIgnoreCase),
			UseDefaultAzureCredential = Flag("use-default-azure-credential") || string.Equals(Environment.GetEnvironmentVariable("MIP_USE_DEFAULT_AZURE_CREDENTIAL"), "true", StringComparison.OrdinalIgnoreCase),
			ManagedIdentityClientId = Arg("managed-identity-client-id", "MIP_MANAGED_IDENTITY_CLIENT_ID", required: false),
		};

		var isDelegatedFlow = !options.UseDefaultAzureCredential && !options.UseCertificateAuth && string.IsNullOrWhiteSpace(options.MipClientSecret);
		if (isDelegatedFlow && string.IsNullOrWhiteSpace(options.MipUserUpn))
		{
			throw new ArgumentException("Delegated flow requires --mip-user-upn (or MIP_USER_UPN) to set the MIP engine identity.");
		}

		if (string.IsNullOrWhiteSpace(options.TargetLabelId) && string.IsNullOrWhiteSpace(options.TargetLabelName) && !options.ListLabels && !options.VerifyOnly)
		{
			throw new ArgumentException("Provide --target-label-id, --target-label-name, use --list-labels, or use --verify-only.");
		}

		return options;
	}
}
